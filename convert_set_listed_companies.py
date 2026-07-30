"""
แก้ปัญหา: SET's listedCompanies_en_US.xls เป็น HTML table ที่ไม่มี <th>
(ใช้ <td> ล้วน) ทำให้ pandas.read_html() เดา header ไม่ได้ และตั้งชื่อ
คอลัมน์เป็น 0,1,2,...,9 แทน

วิธีแก้: อ่านทุก table ด้วย header=None ก่อน แล้วไปหาว่าแถวไหน (ปกติคือ
แถวแรกๆ) ที่มีคำที่คาดว่าจะเป็นชื่อคอลัมน์ (Symbol, Company Name, Market,
Industry, Sector, ...) มากที่สุด ใช้แถวนั้นเป็น header แล้ว slice ข้อมูล
ที่เหลือเป็น body

ใช้ได้ทั้งกับไฟล์ .xls (ที่จริงคือ HTML) และไฟล์ .html ตรงๆ
"""

import re
import sys
import pandas as pd


# คำที่คาดว่าจะเจอในแถว header ของตาราง listed companies บนเว็บ SET
# (พิมพ์เล็ก-ใหญ่ไม่สำคัญ เพราะเราจะ lower() ก่อนเทียบ)
HEADER_KEYWORDS = [
    "symbol",
    "company name",
    "company",
    "market",
    "industry",
    "sector",
    "similar securities",
    "sec",
]


def _score_row_as_header(row) -> int:
    """
    ให้คะแนนว่าแถวนี้ 'น่าจะเป็น header' แค่ไหน
    โดยนับจำนวนเซลล์ในแถวที่มีคำใน HEADER_KEYWORDS ปรากฏอยู่
    """
    score = 0
    for cell in row:
        text = str(cell).strip().lower()
        if not text or text == "nan":
            continue
        for kw in HEADER_KEYWORDS:
            if kw in text:
                score += 1
                break
    return score


def find_header_row_index(df_raw: pd.DataFrame, search_rows: int = 15) -> int:
    """
    ไล่หาแถวที่มีคะแนน header สูงสุดในช่วง search_rows แถวแรกของตาราง
    คืนค่า index (แถวที่ 0-based) ของแถวที่คาดว่าเป็น header

    ถ้าหาไม่เจอเลย (คะแนนสูงสุด = 0) จะ raise เพื่อให้รู้ตัวว่าต้องปรับ
    HEADER_KEYWORDS แทนที่จะเนียนใช้แถวแรกแบบผิดๆ ต่อไป
    """
    n = min(search_rows, len(df_raw))
    best_idx, best_score = None, 0
    for i in range(n):
        score = _score_row_as_header(df_raw.iloc[i].tolist())
        if score > best_score:
            best_idx, best_score = i, score

    if best_idx is None:
        raise ValueError(
            "หา header row ไม่เจอในช่วง search_rows แรก — "
            "ลองเพิ่มค่า search_rows หรือตรวจสอบ/ปรับ HEADER_KEYWORDS "
            "ให้ตรงกับคำที่อยู่ในไฟล์จริง"
        )
    return best_idx


def load_table_with_detected_header(path_or_buffer, table_index: int = None) -> pd.DataFrame:
    """
    อ่านไฟล์ (HTML table ที่แฝงมาในนามสกุล .xls หรือ .html ตรงๆ) แล้ว
    หา header row เองแทนการปล่อยให้ pandas เดา

    ถ้ามีหลาย table ในไฟล์ (มักเจอ table เล็กๆ เช่น legend/footer ปนมาด้วย)
    จะเลือก table ที่มีจำนวนแถวมากที่สุด เว้นแต่ระบุ table_index มาชัดเจน
    """
    # header=None -> อ่านดิบทั้งหมด ไม่ให้ pandas เดา header เอง
    tables = pd.read_html(path_or_buffer, header=None)

    if table_index is not None:
        df_raw = tables[table_index]
    else:
        # เลือก table ที่ใหญ่ที่สุด (แถวเยอะสุด) เพราะ table ข้อมูลหลัก
        # ของหุ้นทั้งตลาดควรจะมีแถวเยอะกว่า table ประกอบอื่นๆ ในหน้าเว็บมาก
        df_raw = max(tables, key=len)

    header_idx = find_header_row_index(df_raw)

    header_row = df_raw.iloc[header_idx].tolist()
    # ทำความสะอาดชื่อคอลัมน์: strip, รวม whitespace ซ้ำ, กัน NaN/duplicate
    clean_headers = []
    seen = {}
    for h in header_row:
        name = re.sub(r"\s+", " ", str(h).strip())
        if not name or name.lower() == "nan":
            name = "col"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        clean_headers.append(name)

    df = df_raw.iloc[header_idx + 1:].copy()
    df.columns = clean_headers
    df = df.reset_index(drop=True)

    # ตัดแถวที่ว่างทั้งแถว (มักเจอท้ายตารางหรือแถวคั่น)
    df = df.dropna(how="all")

    return df


def main():
    if len(sys.argv) < 3:
        print(
            "วิธีใช้: python convert_set_listed_companies.py "
            "<input listedCompanies_en_US.xls> <output thailand_all_tickers.csv>"
        )
        sys.exit(1)

    src, dst = sys.argv[1], sys.argv[2]

    df = load_table_with_detected_header(src)

    print(f"พบ header row, คอลัมน์ที่ตรวจพบ: {list(df.columns)}")
    print(f"จำนวนแถวข้อมูล (ตัวหุ้น): {len(df)}")
    print(df.head(5).to_string())

    df.to_csv(dst, index=False, encoding="utf-8-sig")
    print(f"\nบันทึกไฟล์เรียบร้อย: {dst}")


if __name__ == "__main__":
    main()
