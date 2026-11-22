' Runs PMic with no console window (pythonw).
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
pmicDir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = pmicDir
shell.Run "pythonw """ & pmicDir & "\server.py"" --port=8787", 0, False
