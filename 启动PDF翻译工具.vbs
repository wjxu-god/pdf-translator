Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
base = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = base
sh.Run """pythonw.exe"" """ & base & "\pdf_translator_app.py""", 0, False
