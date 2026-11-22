"""Windows default recording device control.

Uses the (undocumented but widely used) IPolicyConfig COM interface, the same
mechanism as SoundSwitch/EarTrumpet. Everything degrades gracefully.
"""
from __future__ import annotations

import ctypes
import re
from ctypes import HRESULT, POINTER, c_int, c_void_p, c_wchar_p

import comtypes
from comtypes import CLSCTX_ALL, COMMETHOD, GUID, IUnknown

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
IID_IMMDeviceEnumerator = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
IID_IMMDevice = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
CLSID_PolicyConfigClient = GUID("{870af99c-171d-4f9e-af0d-e63df40c2bc9}")
IID_IPolicyConfig = GUID("{f8679f50-850a-41cf-a9c4-7e365c1b9df7}")
IID_IPolicyConfigVista = GUID("{ca286fc3-91fd-42c3-8e9b-caafa66241f1}")

PROTOTYPE_GETID = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_wchar_p))


class IMMDevice(IUnknown):
    _iid_ = IID_IMMDevice
    _methods_ = [
        COMMETHOD([], HRESULT, "Activate"),
        COMMETHOD([], HRESULT, "OpenPropertyStore"),
        # GetId (index 5) is called through raw ctypes - see _com_get_id
        COMMETHOD([], HRESULT, "GetState"),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387C5E}")
    _methods_ = [
        COMMETHOD([], HRESULT, "GetCount", (["out"], POINTER(c_int), "pCount")),
        COMMETHOD([], HRESULT, "Item",
                  (["in"], c_int, "nDevice"),
                  (["out"], POINTER(POINTER(IMMDevice)), "pDevice")),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = IID_IMMDeviceEnumerator
    _methods_ = [
        COMMETHOD([], HRESULT, "EnumAudioEndpoints",
                  (["in"], c_int, "dataFlow"),
                  (["in"], c_int, "dwStateMask"),
                  (["out"], POINTER(POINTER(IMMDeviceCollection)), "ppDevices")),
        COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint",
                  (["in"], c_int, "dataFlow"),
                  (["in"], c_int, "role"),
                  (["out"], POINTER(POINTER(IMMDevice)), "ppEndpoint")),
    ]


def _com_get_id(dev) -> str:
    """IMMDevice::GetId (vtable index 5) via raw ctypes."""
    ptr = ctypes.cast(dev, c_void_p)
    vtbl_ptr = ctypes.cast(ptr, POINTER(c_void_p)).contents.value
    vtbl = ctypes.cast(vtbl_ptr, POINTER(c_void_p))
    fn = ctypes.WINFUNCTYPE(HRESULT, c_void_p, POINTER(c_wchar_p))(vtbl[5])
    out = c_wchar_p()
    hr = fn(ptr.value, ctypes.byref(out))
    if hr != 0 or not out.value:
        return ""
    s = out.value
    ctypes.windll.ole32.CoTaskMemFree(ctypes.cast(out, c_void_p))
    return s


def _policy_config():
    """Return an IPolicyConfig-compatible interface, or None."""
    for iid in (IID_IPolicyConfig, IID_IPolicyConfigVista):
        try:
            methods = [COMMETHOD([], HRESULT, f"m{i}", (["in"], c_void_p, "p"))
                       for i in range(13)]                     # 0..12, never called
            methods.append(COMMETHOD(
                [], HRESULT, "SetDefaultEndpoint",
                (["in"], c_wchar_p, "pszDeviceId"),
                (["in"], c_int, "eRole")))
            methods.append(COMMETHOD([], HRESULT, "SetEndpointVisibility"))
            klass = type(f"_IPolicyConfig_{iid}".replace("-", "_"), (IUnknown,),
                         {"_iid_": iid, "_methods_": methods})
            return comtypes.CoCreateInstance(CLSID_PolicyConfigClient,
                                             interface=klass, clsctx=CLSCTX_ALL)
        except Exception:
            continue
    return None


def _friendly_name(device_id: str) -> str:
    """Endpoint friendly name from the MMDevices registry (avoids PROPVARIANT)."""
    import winreg
    m = re.search(r"\{[0-9A-Fa-f\-]{36}\}$", device_id)
    if not m:
        return ""
    key_path = (rf"SOFTWARE\Microsoft\Windows\CurrentVersion\MMDevices"
                rf"\Audio\Capture\{m.group(0)}\Properties")
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as k:
            for value_name in ("{a45c254e-df1c-4efd-8020-67d146a850e0},14",
                               "{a45c254e-df1c-4efd-8020-67d146a850e0},2"):
                try:
                    value, _ = winreg.QueryValueEx(k, value_name)
                    if value:
                        return value
                except OSError:
                    continue
            return ""
    except OSError:
        return ""


def list_capture_endpoints(active_only: bool = True) -> list[tuple[str, str]]:
    """[(endpoint id, friendly name)] for recording devices."""
    out = []
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator,
                                         interface=IMMDeviceEnumerator,
                                         clsctx=CLSCTX_ALL)
        coll = enum.EnumAudioEndpoints(1, 1 if active_only else 3)
        for i in range(coll.GetCount()):
            dev = coll.Item(i)
            dev_id = _com_get_id(dev)
            if dev_id:
                out.append((dev_id, _friendly_name(dev_id)))
    except Exception:
        pass
    return out


def get_default_capture_id(role: int = 0) -> str | None:
    try:
        enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator,
                                         interface=IMMDeviceEnumerator,
                                         clsctx=CLSCTX_ALL)
        dev = enum.GetDefaultAudioEndpoint(1, role)   # eCapture, eConsole
        return _com_get_id(dev)
    except Exception:
        return None


def set_default_capture(device_id: str, roles=(0, 1, 2)) -> bool:
    """Make device_id the default recording device. True on success."""
    pc = _policy_config()
    if pc is None:
        return False
    ok = False
    for role in roles:
        try:
            hr = pc.SetDefaultEndpoint(device_id, role)
            if hr == 0:
                ok = True
        except Exception:
            pass
    return ok
