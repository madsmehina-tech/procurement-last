' PO Web - starts the server with NO window. Kept for people who start it by hand; the automatic task uses start_server_loop.bat.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
sh.CurrentDirectory = root
If Not fso.FolderExists(root & "\logs") Then fso.CreateFolder(root & "\logs")
sh.Run "cmd /c """ & root & "\deploy\start_server_loop.bat""", 0, False
