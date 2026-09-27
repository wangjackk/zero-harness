import sqlite3
db = sqlite3.connect(r'd:\kshell\zero-harness\zero-py\runtime\xml_agent\memory.db')
for r in db.execute("SELECT role, substr(content,1,220), images FROM messages WHERE session_id='2c9caef3929c426db6f8348b40bc5f15' ORDER BY ts"):
    print(r)
