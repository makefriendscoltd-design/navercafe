' 댓글봇 런처를 '콘솔 창 없이' 백그라운드로 실행한다.
' run_comment_bot.bat 가 python 실행 + 크래시 시 30초 후 자동 재시작 루프를 담당.
' 콘솔 창이 없으므로 실수로 창을 닫아 봇이 죽는 일이 없다.
' (크롬 창은 봇 감지 회피상 보이게 뜨지만, 닫혀도 bat 가 다시 띄운다)
Set sh = CreateObject("WScript.Shell")
sh.CurrentDirectory = "D:\coding\ccidacafe"
sh.Run "cmd /c run_comment_bot.bat", 0, False
