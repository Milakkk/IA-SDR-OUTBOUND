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
emp_cols = [r[1].lower() for r in cur.fetchall()]
cur.execute(f"PRAGMA table_info('{estab_tab}')")
estab_cols = [r[1].lower() for r in cur.fetchall()]
sec_cols = []
if sec_tab:
    cur.execute(f"PRAGMA table_info('{sec_tab}')")
    sec_cols = [r[1].lower() for r in cur.fetchall()]

def has(cols,*names):
    for n in names:
        if n.lower() in cols:
            return n
    return None

cnpj_single = has(estab_cols,'cnpj')
cnpj_b = has(estab_cols,'cnpj_basico')
cnpj_o = has(estab_cols,'cnpj_ordem')
cnpj_d = has(estab_cols,'cnpj_dv')
cnpj_expr = cnpj_single if cnpj_single else (f"{cnpj_b} || {cnpj_o} || {cnpj_d}" if cnpj_b and cnpj_o and cnpj_d else None)

rs_emp = has(emp_cols,'razao_social','nome_empresarial')
nf_estab = has(estab_cols,'nome_fantasia')
cnae_pri = has(estab_cols,'cnae_fiscal','cnae_principal')
uf = has(estab_cols,'uf')
mun = has(estab_cols,'municipio','codigo_municipio')
cep = has(estab_cols,'cep')
logr = has(estab_cols,'logradouro')
num = has(estab_cols,'numero')
bairro = has(estab_cols,'bairro')
email = has(estab_cols,'email','email_do_estabelecimento','correio_eletronico')
ddd1 = has(estab_cols,'ddd1')
tel1 = has(estab_cols,'telefone1','tel1')
ddd2 = has(estab_cols,'ddd2')
tel2 = has(estab_cols,'telefone2','tel2')
sit = has(estab_cols,'situacao_cadastral','situacao')

if not cnpj_expr or not rs_emp or not nf_estab or not cnae_pri:
    print({'erro':'colunas_essenciais_ausentes','cnpj_expr':cnpj_expr,'rs_emp':rs_emp,'nf_estab':nf_estab,'cnae_pri':cnae_pri})
    sys.exit(1)

phone_exprs = []
if ddd1 and tel1:
    phone_exprs.append(f"({ddd1}||'-'||{tel1})")
if ddd2 and tel2:
    phone_exprs.append(f"({ddd2}||'-'||{tel2})")
phone_sql = (' || '.join(phone_exprs)) if phone_exprs else (tel1 or tel2 or 'NULL')

sel_cols = [
    f"{cnpj_expr} AS cnpj",
    f"{rs_emp} AS razao_social",
    f"{nf_estab} AS nome_fantasia",
    f"{cnae_pri} AS cnae_principal",
]
for c,n in [(uf,'uf'),(mun,'municipio'),(cep,'cep'),(logr,'logradouro'),(num,'numero'),(bairro,'bairro'),(email,'email')]:
    sel_cols.append((f"{c} AS {n}") if c else (f"NULL AS {n}"))
sel_cols.append(f"{phone_sql} AS telefone")
sel = ",\n  ".join(sel_cols)

kw = ['estufa','casa de vegetação','viveiro','hidroponia','hidropônica','clonagem','propagação','microverde','microgreens','horticultura','floricultura','eucalipto','pinus','fazenda','sítio','agro']
kw_like = [f"LOWER(emp.{rs_emp}) LIKE ?" for _ in kw] + [f"LOWER(e.{nf_estab}) LIKE ?" for _ in kw]
params = [f"%{k}%" for k in kw] + [f"%{k}%" for k in kw]
where_parts = []
where_parts = where_parts
where_name = f"( {' OR '.join(kw_like)} )"
strict = os.environ.get('STRICT')=='1'
agro_like_sec = ' OR '.join([f"e.cnae_fiscal_secundaria LIKE '%{c}%'" for c in ['0210101','0210103','0210106','0142300','0121101','0122900','0134200','0113000','0141501']])
agro_cond = (f"(substr(e.{cnae_pri},1,2) IN ('01','02'))" if strict else (f"(substr(e.{cnae_pri},1,2) IN ('01','02') OR {agro_like_sec})"))
sit_filter = (f"e.{sit}='02'" if sit else "1=1")

emp_key = 'cnpj_basico'
est_key = 'cnpj_basico'

limit = int(os.environ.get('LIMIT','0'))
qA = f"SELECT DISTINCT\n  {sel}\nFROM {estab_tab} e JOIN {emp_tab} emp ON emp.{emp_key} = e.{est_key}\nWHERE {sit_filter} AND {agro_cond} AND {where_name}"
if limit:
    qA += f"\nLIMIT {limit}"
if os.environ.get('DEBUG')=='1':
    print({'qA':qA})
cur.execute(qA, params)
rowsA = cur.fetchall()
if os.environ.get('DEBUG')=='1':
    print({'rowsA':len(rowsA)})

outA = os.path.join(os.getcwd(),'leads_por_nome_estrito.csv' if strict else 'leads_por_nome.csv')
with open(outA,'w',newline='',encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['cnpj','razao_social','nome_fantasia','cnae_principal','uf','municipio','cep','logradouro','numero','bairro','email','telefone'])
    for r in rowsA:
        w.writerow([r['cnpj'],r['razao_social'],r['nome_fantasia'],r['cnae_principal'],r['uf'],r['municipio'],r['cep'],r['logradouro'],r['numero'],r['bairro'],r['email'],r['telefone']])

cn_map = ['0210101','0210103','0210106','0142300','0121101','0122900','0134200','0113000','0141501']
cond_pri = f"e.{cnae_pri} IN ({','.join('?'*len(cn_map))})"
cond_sec = ''
if sec_tab and has(sec_cols,'cnae'):
    cb = has(estab_cols,'cnpj_basico')
    co = has(estab_cols,'cnpj_ordem')
    cd = has(estab_cols,'cnpj_dv')
    cs_cnae = has(sec_cols,'cnae')
    cs_cb = has(sec_cols,'cnpj_basico') or cb
    cs_co = has(sec_cols,'cnpj_ordem') or co
    cs_cd = has(sec_cols,'cnpj_dv') or cd
    if cb and co and cd and cs_cb and cs_co and cs_cd:
        cond_sec = f"EXISTS (SELECT 1 FROM {sec_tab} cs WHERE cs.{cs_cb}=e.{cb} AND cs.{cs_co}=e.{co} AND cs.{cs_cd}=e.{cd} AND cs.{cs_cnae} IN ({','.join('?'*len(cn_map))}))"

conds = [cond_pri]
if cond_sec:
    conds.append(cond_sec)
whereB = ' OR '.join(conds)
qB = f"SELECT DISTINCT\n  {sel}\nFROM {estab_tab} e JOIN {emp_tab} emp ON emp.{emp_key} = e.{est_key}\nWHERE ({whereB})"
if limit:
    qB += f"\nLIMIT {limit}"
cur.execute(qB, cn_map + cn_map if cond_sec else cn_map)
rowsB = cur.fetchall()

outB = os.path.join(os.getcwd(),'leads_por_cnae.csv')
with open(outB,'w',newline='',encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(['cnpj','razao_social','nome_fantasia','cnae_principal','uf','municipio','cep','logradouro','numero','bairro','email','telefone'])
    for r in rowsB:
        w.writerow([r['cnpj'],r['razao_social'],r['nome_fantasia'],r['cnae_principal'],r['uf'],r['municipio'],r['cep'],r['logradouro'],r['numero'],r['bairro'],r['email'],r['telefone']])

print({'rows_por_nome':len(rowsA),'rows_por_cnae':len(rowsB),'outA':outA,'outB':outB})