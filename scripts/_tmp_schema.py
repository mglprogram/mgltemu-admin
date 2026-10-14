# -*- coding: utf-8 -*-
import sqlite3, sys
sys.stdout.reconfigure(encoding="utf-8", errors="replace")
for store in ("bel", "bhy"):
    p = r"D:\AI-workplace\TEMU\data\%s\%s_products.db" % (store, store)
    con = sqlite3.connect(p)
    print("=" * 20, store)
    for name, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
        print("--", name)
        print(sql)
        n = con.execute("SELECT COUNT(*) FROM %s" % name).fetchone()[0]
        print("   rows:", n)
        cur = con.execute("SELECT * FROM %s LIMIT 2" % name)
        cols = [d[0] for d in cur.description]
        print("   cols:", cols)
        for row in cur:
            print("   ", row)
    con.close()
