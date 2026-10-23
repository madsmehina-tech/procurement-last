' PO Web - starts the server with NO window (used by the auto-start task). Output goes to logs\server.out.txt
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
sh.CurrentDirectory = root
If Not fso.FolderExists(root & "\logs") Then fso.CreateFolder(root & "\logs")
sh.Environment("PROCESS")("PYTHONUTF8") = "1"
sh.Run "cmd /c python serve.py >> logs\server.out.txt 2>&1", 0, False
