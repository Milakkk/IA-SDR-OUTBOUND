import sqlite3
import csv
import os
DB = r"C:\db_cnpj\cnpj.db"
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
cur = conn.cursor()
q = "SELECT e.cnpj, emp.razao_social, e.nome_fantasia, e.cnae_fiscal FROM estabelecimento e JOIN empresas emp ON emp.cnpj_basico=e.cnpj_basico WHERE e.cnae_fiscal IN ('0210101','0210103','0210106','0142300','0121101','0122900','0134200','0113000','0141501') LIMIT 20"
rows = cur.execute(q).fetchall()
print({'rows':len(rows)})
for r in rows:
    print(dict(r))