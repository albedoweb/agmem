# agmem demo — rendered with `vhs demo.tape` (github.com/charmbracelet/vhs)
Output docs/demo/agmem-demo.gif

Set FontSize 17
Set Width 1180
Set Height 720
Set Padding 16
Set Theme "Catppuccin Mocha"
Set TypingSpeed 35ms

Hide
Type "cd /tmp/agmem-demo/payments-infra && export PATH=$HOME/.local/bin:$PATH && rm -rf .agmem && agmem --help >/dev/null 2>&1 && clear"
Enter
Sleep 1s
Show

Type "# your repo already knows the answers — index it once"
Sleep 500ms
Enter
Sleep 1s
Type "agmem init && agmem index"
Sleep 500ms
Enter
Sleep 5s

Type "# teach it the things code can't say"
Sleep 500ms
Enter
Sleep 1s
Type `agmem remember "For bastion hosts in front of AWS data services, prefer modules/aws/rds-bastion-ec2 over generic ec2-instance." --kind rule`
Sleep 500ms
Enter
Sleep 4s

Type "# then ask — from any coding agent, in milliseconds"
Sleep 500ms
Enter
Sleep 1s
Type `agmem context "rds bastion ec2 instance" -n 4`
Sleep 500ms
Enter
Sleep 7s

Type "# plain text all the way down"
Sleep 500ms
Enter
Sleep 1s
Type "head -c 280 .agmem/memories.jsonl"
Sleep 500ms
Enter
Sleep 5s
