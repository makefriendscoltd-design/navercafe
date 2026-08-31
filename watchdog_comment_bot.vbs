' watchdog_comment_bot.ps1 을 콘솔 창 없이(숨김) 호출한다.
' 작업스케줄러가 5분마다 이 vbs 를 실행하므로, 콘솔 깜빡임이 안 보이게 wscript 로 감싼다.
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = scriptDir
sh.Run "powershell -NoProfile -ExecutionPolicy Bypass -File """ & scriptDir & "\watchdog_comment_bot.ps1""", 0, False
