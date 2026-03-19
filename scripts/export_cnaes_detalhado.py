import sqlite3
import csv
import os
import sys

DB = r"C:\db_cnpj\cnpj.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
lt = {t.lower(): t for t in tables}

def pick_table(cands):
    for c in cands:
        if c.lower() in lt:
            return lt[c.lower()]
    return None

emp_tab = pick_table(['empresas','empresa'])
estab_tab = pick_table(['estabelecimentos','estabelecimento'])
sec_tab = pick_table(['cnaes_secundarias','cnae_secundaria','cnaes'])
if not emp_tab or not estab_tab:
    print({'erro':'tabelas_ausentes','disponiveis':tables})
    sys.exit(1)

cur.execute(f"PRAGMA table_info('{emp_tab}')")
emp_cols = [r[1] for r in cur.fetchall()]
cur.execute(f"PRAGMA table_info('{estab_tab}')")
estab_cols = [r[1] for r in cur.fetchall()]
sec_cols = []
if sec_tab:
    cur.execute(f"PRAGMA table_info('{sec_tab}')")
    sec_cols = [r[1] for r in cur.fetchall()]

def has(cols,*names):
    for n in names:
        if n in cols or n.lower() in [c.lower() for c in cols]:
            return n
    return None

cnpj_b = has(estab_cols,'cnpj_basico')
cnpj_o = has(estab_cols,'cnpj_ordem')
cnpj_d = has(estab_cols,'cnpj_dv')
cnae_pri = has(estab_cols,'cnae_fiscal','cnae_principal')
emp_key = has(emp_cols,'cnpj_basico') or 'cnpj_basico'
est_key = has(estab_cols,'cnpj_basico') or 'cnpj_basico'

cs_cb = has(sec_cols,'cnpj_basico') or cnpj_b
cs_co = has(sec_cols,'cnpj_ordem') or cnpj_o
cs_cd = has(sec_cols,'cnpj_dv') or cnpj_d
cs_cnae = has(sec_cols,'cnae')

codes = ['0210101','0210103','0210106','0142300','0141501','0121101','0122900','0134200','0113000']

def sec_exists_sql():
    if not sec_tab or not cs_cnae or not cnpj_b or not cnpj_o or not cnpj_d:
        return None
    placeholders = ','.join('?'*len(codes))
    return f"EXISTS (SELECT 1 FROM {sec_tab} s WHERE s.{cs_cb}=e.{cnpj_b} AND s.{cs_co}=e.{cnpj_o} AND s.{cs_cd}=e.{cnpj_d} AND s.{cs_cnae} IN ({placeholders}))"

def sec_group_sql():
    if not sec_tab or not cs_cnae or not cnpj_b or not cnpj_o or not cnpj_d:
        return "NULL"
    return f"(SELECT GROUP_CONCAT(s.{cs_cnae},'|') FROM {sec_tab} s WHERE s.{cs_cb}=e.{cnpj_b} AND s.{cs_co}=e.{cnpj_o} AND s.{cs_cd}=e.{cnpj_d})"

if not cnae_pri or not cnpj_b or not cnpj_o or not cnpj_d:
    print({'erro':'colunas_essenciais_ausentes','cnae_pri':cnae_pri,'cnpj_basico':cnpj_b,'cnpj_ordem':cnpj_o,'cnpj_dv':cnpj_d})
    sys.exit(1)

pri_cond = f"e.{cnae_pri} IN ({','.join('?'*len(codes))})"
sec_cond = sec_exists_sql()
where = pri_cond if not sec_cond else f"({pri_cond}) OR ({sec_cond})"

emp_select = ','.join([f"emp.{c} AS emp__{c}" for c in emp_cols])
est_select = ','.join([f"e.{c} AS est__{c}" for c in estab_cols])
sec_group = sec_group_sql()
sel = f"{est_select}, {emp_select}, {sec_group} AS cnaes_secundarios"

q = f"SELECT {sel} FROM {estab_tab} e JOIN {emp_tab} emp ON emp.{emp_key}=e.{est_key} WHERE {where}"
params = codes + (codes if sec_cond else [])
cur.execute(q, params)
rows = cur.fetchall()

out = os.path.join(os.getcwd(),'leads_por_cnae_detalhado.csv')
with open(out,'w',newline='',encoding='utf-8') as f:
    w = csv.writer(f)
    headers = [f"est__{c}" for c in estab_cols] + [f"emp__{c}" for c in emp_cols] + ['cnaes_secundarios']
    w.writerow(headers)
    for r in rows:
        w.writerow([r[h] for h in headers])

print({'rows':len(rows),'out':out})