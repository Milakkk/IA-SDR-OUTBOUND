import os
import re
import requests
def login():
    ver = os.getenv('SALESFORCE_API_VERSION','59.0')
    u = os.getenv('SALESFORCE_USERNAME')
    p = os.getenv('SALESFORCE_PASSWORD')
    t = os.getenv('SALESFORCE_SECURITY_TOKEN')
    if not u or not p or not t:
        return {'ok':False,'erro':'credenciais_ausentes'}
    endpoint = f'https://login.salesforce.com/services/Soap/u/{ver}'
    body = f"""
    <env:Envelope xmlns:xsd="http://www.w3.org/2001/XMLSchema" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xmlns:env="http://schemas.xmlsoap.org/soap/envelope/">
      <env:Header>
        <n1:LoginScopeHeader xmlns:n1="urn:partner.soap.sforce.com"></n1:LoginScopeHeader>
      </env:Header>
      <env:Body>
        <n1:login xmlns:n1="urn:partner.soap.sforce.com">
          <n1:username>{u}</n1:username>
          <n1:password>{p}{t}</n1:password>
        </n1:login>
      </env:Body>
    </env:Envelope>
    """.strip()
    r = requests.post(endpoint, data=body, headers={'Content-Type':'text/xml; charset=UTF-8', 'SOAPAction':'login'})
    txt = r.text
    sid = None
    surl = None
    m1 = re.search(r"<sessionId>([^<]+)</sessionId>", txt)
    m2 = re.search(r"<serverUrl>([^<]+)</serverUrl>", txt)
    if m1:
        sid = m1.group(1)
    if m2:
        surl = m2.group(1)
    if not sid or not surl:
        return {'ok':False,'erro':'falha_login','detalhe':txt[:200]}
    return {'ok':True,'sessionId':sid,'serverUrl':surl}
def instance_host(server_url: str):
    m = re.match(r"https://([^/]+)/", server_url)
    return m.group(1) if m else None
def create_lead(data):
    ver = os.getenv('SALESFORCE_API_VERSION','59.0')
    lg = login()
    if not lg.get('ok'):
        return {'ok':False,'erro':lg}
    host = instance_host(lg['serverUrl'])
    if not host:
        return {'ok':False,'erro':'host_indisponivel'}
    url = f"https://{host}/services/data/v{ver}/sobjects/Lead"
    oid = os.getenv('SALESFORCE_OWNER_ID') or ''
    payload = dict(data)
    if oid:
        payload['OwnerId'] = oid
    r = requests.post(url, json=payload, headers={'Authorization': f'Bearer {lg['sessionId']}', 'Content-Type':'application/json'})
    try:
        j = r.json()
    except Exception:
        j = {'success':False}
    ok = j.get('success') is True
    return {'ok':ok,'result':j}
