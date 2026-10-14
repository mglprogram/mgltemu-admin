import openpyxl, sys, io, collections
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

SRC = r"C:\Users\saing\AppData\Roaming\ziniaobrowserdatas\ziniao browser\TM-BHY-YJF\商品基础信息.xlsx"
wb = openpyxl.load_workbook(SRC, read_only=True, data_only=True)
ws = wb.worksheets[0]
rows = list(ws.iter_rows(values_only=True))
hdr = rows[0]
data = rows[1:]
print("HEADERS:", list(hdr))
print("rows:", len(data))
for idx in (0, 3, 6, 7, 8, 13, 14):
    c = collections.Counter(r[idx] for r in data)
    print(f"\n[{idx}] {hdr[idx]} -> {len(c)} uniq")
    for k, v in c.most_common(15):
        print("   ", repr(k), v)
