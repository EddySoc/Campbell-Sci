' Start Campbell Sci zonder zichtbaar consolevenster.
Set objShell = CreateObject("WScript.Shell")
scriptDir = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
pythonw = scriptDir & "\.venv\Scripts\pythonw.exe"
objShell.Run """" & pythonw & """ -m campbell_sci.main", 0, False
