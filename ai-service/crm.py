import os
import json
from typing import Dict, Any, Optional

import requests
from simple_salesforce import Salesforce
from dotenv import load_dotenv
from functools import lru_cache
from pathlib import Path


def _load_env_once():
    load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / '.env'))


def _normalize_phone(phone: str) -> str:
    """
    Normaliza telefone removendo todos os caracteres não numéricos.
    Exemplos:
    - (41) 99999-9999 -> 41999999999
    - +55 41 99999-9999 -> 5541999999999
    - 41 99999-9999 -> 41999999999
    """
    if not phone:
        return ""
    return ''.join(filter(str.isdigit, str(phone)))


def _normalize_digits(value: Optional[str]) -> str:
    return ''.join(filter(str.isdigit, str(value or "")))


def _format_cnpj(value: Optional[str]) -> str:
    digits = _normalize_digits(value)
    if len(digits) != 14:
        return digits
    return f"{digits[:2]}.{digits[2:5]}.{digits[5:8]}/{digits[8:12]}-{digits[12:]}"


def _business_unit_for_sector(sector: Optional[str]) -> str:
    sector_low = (sector or "").lower()
    mapping = {
        "industrial": "Industrial",
        "esportivo": "Esportivo",
        "cidades": "Cidades",
        "grow": "Grow",
    }
    return mapping.get(sector_low, "Industrial")


def _industry_for_sector(sector: Optional[str]) -> str:
    sector_low = (sector or "").lower()
    mapping = {
        "industrial": "Manufacturing",
        "esportivo": "Sports & Recreation",
        "cidades": "Government",
        "grow": "Agriculture",
    }
    return mapping.get(sector_low, "Manufacturing")


def _generate_phone_variations(phone: str) -> list:
    """
    Gera variações do telefone para busca mais abrangente.
    Considera diferentes formatos: com/sem DDD, com/sem código do país.
    """
    normalized = _normalize_phone(phone)
    if not normalized:
        return []
    
    variations = [normalized]  # Formato completo normalizado
    
    # Se tem mais de 10 dígitos, pode ter código do país (55)
    if len(normalized) > 10:
        # Remove código do país (55) se presente
        if normalized.startswith('55') and len(normalized) >= 12:
            without_country = normalized[2:]
            variations.append(without_country)
            
            # Se ainda tem mais de 10 dígitos, pode ter DDD
            if len(without_country) > 10:
                # Remove DDD (2 primeiros dígitos)
                without_ddd = without_country[2:]
                if len(without_ddd) >= 8:
                    variations.append(without_ddd)
        else:
            # Sem código do país, mas pode ter DDD
            if len(normalized) > 10:
                # Remove DDD (2 primeiros dígitos)
                without_ddd = normalized[2:]
                if len(without_ddd) >= 8:
                    variations.append(without_ddd)
    elif len(normalized) == 10:
        # Formato com DDD (10 dígitos): 4199999999
        # Adiciona variação sem DDD
        without_ddd = normalized[2:]
        if len(without_ddd) >= 8:
            variations.append(without_ddd)
    elif len(normalized) >= 8:
        # Formato sem DDD (8 ou 9 dígitos): 99999999 ou 999999999
        # Adiciona variações com DDDs comuns (41, 11, 21, etc)
        phone_without_ddd = normalized
        common_ddds = ['41', '11', '21', '47', '48', '51', '61', '71', '85']
        for ddd in common_ddds:
            with_ddd = ddd + phone_without_ddd
            variations.append(with_ddd)
    
    # Remove duplicatas mantendo ordem
    seen = set()
    unique_variations = []
    for v in variations:
        if v not in seen and len(v) >= 8:  # Mínimo 8 dígitos para ser válido
            seen.add(v)
            unique_variations.append(v)
    
    return unique_variations


def search_existing_lead(email: Optional[str] = None, phone: Optional[str] = None, cnpj: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Busca um Lead existente no Salesforce por email, telefone ou CNPJ.
    Retorna o Lead encontrado ou None.
    
    A busca por telefone é robusta e considera múltiplos formatos:
    - (41) 99999-9999
    - 41999999999
    - +55 41 99999-9999
    - 41 99999-9999
    - etc.
    """
    _load_env_once()
    username = os.getenv('SALESFORCE_USERNAME')
    password = os.getenv('SALESFORCE_PASSWORD')
    token = os.getenv('SALESFORCE_SECURITY_TOKEN', '')
    domain = 'login'
    api_version = os.getenv('SALESFORCE_API_VERSION', '59.0')

    if not username or not password:
        return None

    if not email and not phone and not cnpj:
        return None

    try:
        sf = Salesforce(username=username, password=password, security_token=token, domain=domain, version=api_version)
        
        conditions = []
        
        # Busca por email (se fornecido)
        if email:
            safe_email = email.replace("'", "\\'")
            conditions.append(f"Email = '{safe_email}'")
        
        # Busca por telefone com múltiplas variações (se fornecido)
        if phone:
            phone_variations = _generate_phone_variations(phone)
            if phone_variations:
                phone_conditions = []
                for phone_var in phone_variations:
                    # Escapa caracteres especiais para SQL
                    safe_phone = phone_var.replace("'", "\\'")
                    # Busca em Phone e MobilePhone
                    phone_conditions.append(f"(Phone LIKE '%{safe_phone}%' OR MobilePhone LIKE '%{safe_phone}%')")
                
                if phone_conditions:
                    # Agrupa todas as variações de telefone com OR
                    phone_condition = "(" + " OR ".join(phone_conditions) + ")"
                    conditions.append(phone_condition)
        
        # Busca por CNPJ (se fornecido)
        if cnpj:
            clean_cnpj = _normalize_digits(cnpj)
            if clean_cnpj:
                safe_cnpj = clean_cnpj.replace("'", "\\'")
                conditions.append(f"CNPJ__c LIKE '%{safe_cnpj}%'")
        
        if not conditions:
            return None
        
        # Combina todas as condições com OR
        where_clause = " OR ".join(conditions)
        query = f"SELECT Id, Name, Company, Email, Phone, MobilePhone, CNPJ__c, Coment_rio__c, Description FROM Lead WHERE {where_clause} ORDER BY CreatedDate DESC LIMIT 1"
        
        print(f"[SALESFORCE] Buscando lead existente: {where_clause}")
        result = sf.query(query)
        
        if result and result.get('records'):
            lead = result['records'][0]
            print(f"[SALESFORCE] Lead existente encontrado: {lead.get('Id')} - {lead.get('Name')} ({lead.get('Company')})")
            return {
                'id': lead.get('Id'),
                'name': lead.get('Name'),
                'company': lead.get('Company'),
                'email': lead.get('Email'),
                'phone': lead.get('Phone'),
                'mobile': lead.get('MobilePhone'),
                'cnpj': lead.get('CNPJ__c'),
                'comments': lead.get('Coment_rio__c', ''),
                'description': lead.get('Description', '')
            }
        
        print(f"[SALESFORCE] Nenhum lead existente encontrado")
        return None
        
    except Exception as e:
        import traceback
        print(f"[SALESFORCE] Erro ao buscar lead existente: {e}")
        print(traceback.format_exc())
        return None


def _load_field_mapping() -> Dict[str, Any]:
    """Carrega o mapeamento de campos do Salesforce"""
    mapping_path = Path(__file__).resolve().parent / 'salesforce_fields_mapping.json'
    try:
        if mapping_path.exists():
            with open(mapping_path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


def _map_data_to_salesforce_fields(data: Dict[str, Any], sector: Optional[str] = None) -> Dict[str, Any]:
    """
    Mapeia os dados coletados para os campos do Salesforce de acordo com o setor.
    """
    mapping = _load_field_mapping()
    sector_mapping = mapping.get('sector_mapping', {}).get(sector or 'industrial', {})
    
    lead_payload = {}
    
    # Mapeia campos padrão
    field_mapping = {
        'Company': sector_mapping.get('Company', ['company', 'empresa']),
        'FirstName': sector_mapping.get('FirstName', ['contact_name', 'nome']),
        'LastName': sector_mapping.get('LastName', ['sobrenome', 'last_name']),
        'Title': sector_mapping.get('Title', ['cargo', 'title']),
        'Email': sector_mapping.get('Email', ['email']),
        'Phone': sector_mapping.get('Phone', ['phone', 'telefone']),
        'MobilePhone': sector_mapping.get('MobilePhone', ['mobile', 'celular', 'whatsapp']),
        'City': sector_mapping.get('City', ['city', 'cidade']),
        'State': sector_mapping.get('State', ['state', 'estado', 'uf']),
        'PostalCode': sector_mapping.get('PostalCode', ['cep', 'postal_code']),
        'Street': sector_mapping.get('Street', ['endereco', 'rua', 'street', 'localizacao']),
        'Website': ['website', 'site'],
    }
    
    # Preenche campos padrão
    for sf_field, data_keys in field_mapping.items():
        value = None
        for key in data_keys:
            # Tenta encontrar o valor nos dados (case insensitive)
            for data_key, data_value in data.items():
                if data_key.lower() == key.lower() and data_value:
                    value = data_value
                    break
            if value:
                break
        if value:
            lead_payload[sf_field] = str(value)
    
    # Campos fixos por setor
    if sector_mapping.get('Industry'):
        lead_payload['Industry'] = sector_mapping['Industry']
    if sector_mapping.get('LeadSource'):
        lead_payload['LeadSource'] = data.get('source') or sector_mapping['LeadSource']
    if sector_mapping.get('Status'):
        lead_payload['Status'] = data.get('status') or sector_mapping['Status']
    
    # Preenche campos customizados (se existirem)
    # Lista de campos customizados que NÃO existem no Salesforce e devem ser ignorados
    invalid_custom_fields = ['Segmento__c']
    custom_fields = sector_mapping.get('custom_fields', {})
    for sf_custom_field, data_keys in custom_fields.items():
        # Pula campos customizados inválidos
        if sf_custom_field in invalid_custom_fields:
            print(f"[SALESFORCE] [AVISO] Pulando campo customizado invalido: {sf_custom_field}")
            continue
        value = None
        for key in data_keys:
            for data_key, data_value in data.items():
                if data_key.lower() == key.lower() and data_value:
                    value = data_value
                    break
            if value:
                break
        if value:
            lead_payload[sf_custom_field] = str(value)
    
    # Adiciona CNPJ se disponível (para todos os setores)
    # Mapeia para CNPJ__c e Doc_CNPJ__c apenas se foi coletado
    cnpj_value = data.get('cnpj') or data.get('CNPJ')
    if cnpj_value:
        clean_cnpj = _normalize_digits(str(cnpj_value))
        if clean_cnpj:
            lead_payload['CNPJ__c'] = clean_cnpj
            lead_payload['Doc_CNPJ__c'] = _format_cnpj(clean_cnpj)
            print(f"[SALESFORCE] CNPJ adicionado: {clean_cnpj}")
    
    # Description com todos os dados relevantes (sem duplicação)
    description_fields = sector_mapping.get('Description', [])
    description_parts = []
    processed_keys = set()
    
    # Adiciona dados específicos do setor
    for field in description_fields:
        for data_key, data_value in data.items():
            if data_key.lower() in processed_keys:
                continue
            field_lower = field.lower() if isinstance(field, str) else None
            if field_lower and field_lower in data_key.lower():
                if data_value:
                    description_parts.append(f"{data_key}: {data_value}")
                    processed_keys.add(data_key.lower())
    
    # Adiciona outros dados não mapeados (apenas uma vez)
    mapped_keys = set()
    for keys_list in field_mapping.values():
        mapped_keys.update([k.lower() for k in keys_list])
    for key in ['source', 'status', 'sector', 'contact_name', 'nome', 'email', 'phone', 'mobile', 'company', 'empresa', 'city', 'cidade', 'state', 'estado', 'uf', 'cep', 'endereco', 'rua', 'cargo', 'title']:
        mapped_keys.add(key.lower())
    
    other_data = {}
    for k, v in data.items():
        if k.lower() not in mapped_keys and k.lower() not in processed_keys and v is not None:
            other_data[k] = v
    
    if other_data:
        description_parts.append(f"Dados adicionais: {json.dumps(other_data, ensure_ascii=False)}")
    
    if description_parts:
        lead_payload['Description'] = '\n'.join(description_parts)
    else:
        # Fallback: descrição com todos os dados (sem duplicação)
        unique_data = {}
        seen_keys = set()
        for k, v in data.items():
            if k.lower() not in seen_keys and v is not None:
                unique_data[k] = v
                seen_keys.add(k.lower())
        lead_payload['Description'] = json.dumps(unique_data, ensure_ascii=False)
    
    # Valores padrão se campos obrigatórios estiverem vazios
    if not lead_payload.get('Company'):
        lead_payload['Company'] = data.get('segment') or data.get('empresa') or 'Prospect'
    if not lead_payload.get('LastName'):
        # Tenta separar nome completo
        contact_name = data.get('contact_name') or data.get('nome') or ''
        if contact_name:
            parts = contact_name.split()
            if len(parts) > 1:
                lead_payload['FirstName'] = parts[0]
                lead_payload['LastName'] = ' '.join(parts[1:])
            else:
                lead_payload['LastName'] = contact_name
        else:
            lead_payload['LastName'] = data.get('sector') or 'Contato'
    
    # Campos obrigatórios do Salesforce (valores padrão)
    if 'Country' not in lead_payload or not lead_payload.get('Country'):
        lead_payload['Country'] = 'Brasil'
    if 'ICP__c' not in lead_payload or not lead_payload.get('ICP__c'):
        lead_payload['ICP__c'] = 'Sim'
    if 'BU_Business_Unit__c' not in lead_payload or not lead_payload.get('BU_Business_Unit__c'):
        lead_payload['BU_Business_Unit__c'] = _business_unit_for_sector(sector or data.get('sector'))
    
    # Campos obrigatórios específicos para BU Industrial
    if lead_payload.get('BU_Business_Unit__c') == 'Industrial':
        # Segmento_Industrial__c - mapeia segmento coletado
        if 'Segmento_Industrial__c' not in lead_payload or not lead_payload.get('Segmento_Industrial__c'):
            segmento = data.get('segmento') or data.get('segmento_empresa') or ''
            # Mapeia segmentos comuns para valores válidos
            segmento_map = {
                'metalurgica': 'Metalurgia (Não Ferrosos)',
                'metalurgia': 'Metalurgia (Não Ferrosos)',
                'siderurgia': 'Siderurgia (Aço)',
                'automotiva': 'Automotiva (Veículos)',
                'alimentos': 'Alimentos',
                'textil': 'Têxtil',
                'quimica': 'Química',
                'construcao': 'Construção',
                'logistica': 'Logística',
                'varejo': 'Varejo'
            }
            segmento_low = segmento.lower()
            if segmento_low in segmento_map:
                lead_payload['Segmento_Industrial__c'] = segmento_map[segmento_low]
            else:
                # Usa um valor padrão genérico
                lead_payload['Segmento_Industrial__c'] = 'Metalurgia (Não Ferrosos)'
        
        # Necessidade_Industrial__c - usa a necessidade coletada
        if 'Necessidade_Industrial__c' not in lead_payload or not lead_payload.get('Necessidade_Industrial__c'):
            necessidade = data.get('necessidade') or data.get('produto') or 'Energia solar'
            lead_payload['Necessidade_Industrial__c'] = necessidade
        
        # Faturamento_Industrial__c - usa valor padrão (menor faixa)
        if 'Faturamento_Industrial__c' not in lead_payload or not lead_payload.get('Faturamento_Industrial__c'):
            lead_payload['Faturamento_Industrial__c'] = 'R$ 0 a R$ 360 mil'
        
        # Doc_CNPJ__c - apenas adiciona se foi coletado
        # Não adiciona CNPJ fictício se não foi coletado
    
    if 'Estrat_gia__c' not in lead_payload or not lead_payload.get('Estrat_gia__c'):
        lead_payload['Estrat_gia__c'] = 'Inbound'
    if 'Tipo_de_Pessoa__c' not in lead_payload or not lead_payload.get('Tipo_de_Pessoa__c'):
        # Valores válidos: "Pessoa Física (PF)" ou "Pessoa Jurídica (PJ)"
        # Para leads B2B, sempre usa PJ
        lead_payload['Tipo_de_Pessoa__c'] = 'Pessoa Jurídica (PJ)'
    
    return lead_payload


def add_chatter_to_lead(lead_id: str, text: str) -> bool:
    """
    Adiciona um comentário no chatter do Lead do Salesforce.
    Requer: SALESFORCE_USERNAME, SALESFORCE_PASSWORD, SALESFORCE_SECURITY_TOKEN
    Retorna True se sucesso, False caso contrário.
    """
    _load_env_once()
    username = os.getenv('SALESFORCE_USERNAME')
    password = os.getenv('SALESFORCE_PASSWORD')
    token = os.getenv('SALESFORCE_SECURITY_TOKEN', '')
    domain = 'login'
    api_version = os.getenv('SALESFORCE_API_VERSION', '59.0')

    if not username or not password or not lead_id or not text:
        return False

    try:
        sf = Salesforce(username=username, password=password, security_token=token, domain=domain, version=api_version)
        
        # Adiciona comentário no chatter usando FeedItem
        feed_item = {
            'ParentId': lead_id,
            'Body': text
        }
        
        result = sf.FeedItem.create(feed_item)
        if result.get('success'):
            print(f"[SALESFORCE] Chatter adicionado ao lead {lead_id}")
            return True
        else:
            print(f"[SALESFORCE] Erro ao adicionar chatter: {result.get('errors', [])}")
            return False
    except Exception as e:
        import traceback
        print(f"[SALESFORCE] Erro ao adicionar chatter no Salesforce: {e}")
        print(traceback.format_exc())
        return False


def create_lead_salesforce(data: Dict[str, Any], owner_id: Optional[str] = None, sector: Optional[str] = None, summary: Optional[str] = None) -> Optional[str]:
    """
    Cria um Lead no Salesforce com todos os campos mapeados por setor.
    Se summary for fornecido, adiciona no campo customizado Coment_rio__c do lead.
    Requer: SALESFORCE_USERNAME, SALESFORCE_PASSWORD, SALESFORCE_SECURITY_TOKEN
    Opcional: SALESFORCE_INSTANCE_HOST, SALESFORCE_API_VERSION
    Retorna o ID do Lead ou None se falhar.
    """
    _load_env_once()
    username = os.getenv('SALESFORCE_USERNAME')
    password = os.getenv('SALESFORCE_PASSWORD')
    token = os.getenv('SALESFORCE_SECURITY_TOKEN', '')
    domain = 'login'
    api_version = os.getenv('SALESFORCE_API_VERSION', '59.0')

    if not username or not password:
        return None

    try:
        sf = Salesforce(username=username, password=password, security_token=token, domain=domain, version=api_version)
        sector_low = (sector or data.get('sector', '')).lower()
        bu_value = _business_unit_for_sector(sector or data.get('sector'))
        industry_value = _industry_for_sector(sector or data.get('sector'))
        cnpj_digits = _normalize_digits(data.get('cnpj') or data.get('CNPJ'))
        cnpj_formatted = _format_cnpj(cnpj_digits) if cnpj_digits else ''
        existing_lead = search_existing_lead(
            email=data.get('email'),
            phone=data.get('phone') or data.get('telefone'),
            cnpj=cnpj_digits,
        )

        if existing_lead and existing_lead.get('id'):
            lead_id = existing_lead['id']
            print(f"[SALESFORCE] Reutilizando lead existente: {lead_id}")
            if summary:
                comment_ok = update_lead_comment(lead_id, summary, ai_name="Eduardo")
                chatter_ok = add_chatter_to_lead(lead_id, summary)
                print(
                    f"[SALESFORCE] Lead existente atualizado: "
                    f"Coment_rio__c={'OK' if comment_ok else 'FALHOU'}, "
                    f"Chatter={'OK' if chatter_ok else 'FALHOU'}"
                )
            return lead_id
        
        # CRÍTICO: Cria payload diretamente (sem usar _map_data_to_salesforce_fields)
        # O teste direto funcionou, então vamos criar o payload manualmente
        lead_payload = {
            'LastName': data.get('nome') or data.get('contact_name') or 'Contato',
            'Company': data.get('empresa') or data.get('company') or 'Prospect',
            'Email': data.get('email'),
            'Phone': data.get('phone') or data.get('telefone'),
            'City': data.get('cidade') or data.get('city'),
            'State': data.get('estado') or data.get('state') or data.get('uf'),
            'Country': 'Brasil',  # SEMPRE 'Brasil'
            'ICP__c': 'Sim',
            'BU_Business_Unit__c': bu_value,
            'Estrat_gia__c': 'Inbound',
            'Tipo_de_Pessoa__c': 'Pessoa Jurídica (PJ)',
        }
        if cnpj_digits:
            lead_payload['CNPJ__c'] = cnpj_digits
            lead_payload['Doc_CNPJ__c'] = cnpj_formatted
            print(f"[SALESFORCE] CNPJ coletado e adicionado: {cnpj_formatted}")

        # Adiciona campos obrigatórios para BU Industrial
        if lead_payload.get('BU_Business_Unit__c') == 'Industrial':
            segmento = data.get('segmento') or data.get('segmento_empresa') or ''
            segmento_map = {
                'metalurgica': 'Metalurgia (Não Ferrosos)',
                'metalurgia': 'Metalurgia (Não Ferrosos)',
                'siderurgia': 'Siderurgia (Aço)',
            }
            segmento_low = segmento.lower()
            lead_payload['Segmento_Industrial__c'] = segmento_map.get(segmento_low, 'Metalurgia (Não Ferrosos)')
            lead_payload['Necessidade_Industrial__c'] = data.get('necessidade') or data.get('produto') or 'Energia solar'
            lead_payload['Faturamento_Industrial__c'] = 'R$ 0 a R$ 360 mil'
        # Adiciona campos opcionais
        if data.get('FirstName'):
            lead_payload['FirstName'] = data.get('FirstName')
        if data.get('Title'):
            lead_payload['Title'] = data.get('Title')
        if data.get('Website'):
            lead_payload['Website'] = data.get('Website')
        lead_payload['LeadSource'] = data.get('source') or 'WhatsApp'
        lead_payload['Status'] = data.get('status') or data.get('Status')
        lead_payload['Industry'] = industry_value
        
        # Adiciona owner se fornecido
        if owner_id:
            lead_payload['OwnerId'] = owner_id
        
        print(f"[SALESFORCE] [DEBUG] Payload criado manualmente com {len(lead_payload)} campos")
        print(f"[SALESFORCE] [DEBUG] Country: {repr(lead_payload.get('Country'))}")
        
        # CRÍTICO: Remove campos None ou vazios, mas SEMPRE mantém Country
        # CRÍTICO: Remove CNPJ fictício se presente
        lead_payload_final = {}
        # PRIMEIRO: Sempre adiciona Country
        lead_payload_final['Country'] = 'Brasil'
        # SEGUNDO: Adiciona OwnerId se presente
        if 'OwnerId' in lead_payload and lead_payload.get('OwnerId'):
            lead_payload_final['OwnerId'] = lead_payload['OwnerId']
        # TERCEIRO: Adiciona todos os outros campos (exceto Country e OwnerId que já foram adicionados)
        # CRÍTICO: Remove CNPJ fictício se presente
        for k, v in lead_payload.items():
            if k in ['Country', 'OwnerId']:
                continue  # Já processados acima
            if v is not None and v != '':
                # Remove CNPJ fictício se presente
                if k in ['Doc_CNPJ__c', 'CNPJ__c']:
                    v_str = str(v).replace('.', '').replace('/', '').replace('-', '')
                    if v_str == '00000000000100' or v == '00.000.000/0001-00':
                        print(f"[SALESFORCE] [AVISO] Removendo CNPJ fictício do campo {k}: {v}")
                        continue  # Não adiciona CNPJ fictício
                lead_payload_final[k] = v
        
        # Garante que os campos obrigatórios estão presentes
        if 'BU_Business_Unit__c' not in lead_payload_final:
            lead_payload_final['BU_Business_Unit__c'] = bu_value
        if 'Estrat_gia__c' not in lead_payload_final:
            lead_payload_final['Estrat_gia__c'] = 'Inbound'
        if 'Tipo_de_Pessoa__c' not in lead_payload_final:
            lead_payload_final['Tipo_de_Pessoa__c'] = 'Pessoa Jurídica (PJ)'
        if 'ICP__c' not in lead_payload_final:
            lead_payload_final['ICP__c'] = 'Sim'
        # Campos obrigatórios específicos para BU Industrial
        if lead_payload_final.get('BU_Business_Unit__c') == 'Industrial':
            if 'Segmento_Industrial__c' not in lead_payload_final:
                lead_payload_final['Segmento_Industrial__c'] = 'Metalurgia (Não Ferrosos)'
            if 'Necessidade_Industrial__c' not in lead_payload_final:
                lead_payload_final['Necessidade_Industrial__c'] = 'Energia solar'
            if 'Faturamento_Industrial__c' not in lead_payload_final:
                lead_payload_final['Faturamento_Industrial__c'] = 'R$ 0 a R$ 360 mil'
            
            # Doc_CNPJ__c - apenas adiciona se CNPJ foi coletado (NUNCA adiciona CNPJ fictício)
            if cnpj_digits:
                if cnpj_digits != '00000000000100' and len(cnpj_digits) >= 11:
                    if 'Doc_CNPJ__c' not in lead_payload_final or not lead_payload_final.get('Doc_CNPJ__c'):
                        lead_payload_final['Doc_CNPJ__c'] = cnpj_formatted
                        if 'CNPJ__c' not in lead_payload_final:
                            lead_payload_final['CNPJ__c'] = cnpj_digits
                        print(f"[SALESFORCE] CNPJ coletado e adicionado ao payload final: {cnpj_formatted}")
                else:
                    print(f"[SALESFORCE] [AVISO] CNPJ fictício ou inválido detectado - NÃO adicionando: {cnpj_digits}")
            else:
                print(f"[SALESFORCE] [DEBUG] CNPJ não foi coletado - NÃO adicionando Doc_CNPJ__c")
        
        print(f"[SALESFORCE] Criando lead com {len(lead_payload_final)} campos")
        print(f"[SALESFORCE] [DEBUG] Campos obrigatorios: Country={lead_payload_final.get('Country')}, BU={lead_payload_final.get('BU_Business_Unit__c')}, Estrategia={lead_payload_final.get('Estrat_gia__c')}, TipoPessoa={lead_payload_final.get('Tipo_de_Pessoa__c')}")
        print(f"[SALESFORCE] [DEBUG] Country no payload final: {repr(lead_payload_final.get('Country'))}")
        print(f"[SALESFORCE] [DEBUG] Country type: {type(lead_payload_final.get('Country'))}")
        print(f"[SALESFORCE] [DEBUG] Doc_CNPJ__c no payload final: {repr(lead_payload_final.get('Doc_CNPJ__c'))}")
        print(f"[SALESFORCE] [DEBUG] CNPJ__c no payload final: {repr(lead_payload_final.get('CNPJ__c'))}")
        print(f"[SALESFORCE] [DEBUG] CNPJ nos dados originais: cnpj={repr(data.get('cnpj'))}, CNPJ={repr(data.get('CNPJ'))}")
        
        lead_id = None
        try:
            # ESTRATÉGIA: Cria lead em duas etapas para contornar validações customizadas
            # ETAPA 1: Cria com apenas campos padrão obrigatórios
            basic_payload = {
                'LastName': lead_payload_final.get('LastName') or 'Contato',
                'Company': lead_payload_final.get('Company') or 'Prospect',
                'Country': 'Brasil',  # SEMPRE 'Brasil'
            }
            # Adiciona campos padrão opcionais se disponíveis
            if lead_payload_final.get('Email'):
                basic_payload['Email'] = lead_payload_final['Email']
            if lead_payload_final.get('Phone'):
                basic_payload['Phone'] = lead_payload_final['Phone']
            if lead_payload_final.get('City'):
                basic_payload['City'] = lead_payload_final['City']
            if lead_payload_final.get('State'):
                basic_payload['State'] = lead_payload_final['State']
            if lead_payload_final.get('OwnerId'):
                basic_payload['OwnerId'] = lead_payload_final['OwnerId']
            
            print(f"[SALESFORCE] [DEBUG] Criando lead básico com {len(basic_payload)} campos")
            print(f"[SALESFORCE] [DEBUG] Country: {repr(basic_payload.get('Country'))}")
            
            # Cria o lead básico
            res = sf.Lead.create(basic_payload)
            lead_id = res.get('id')
            
            if not lead_id:
                print(f"[SALESFORCE] [ERRO] Falha ao criar lead básico")
                raise Exception("Falha ao criar lead básico")
            
            print(f"[SALESFORCE] Lead básico criado com sucesso: {lead_id}")
            
            # ETAPA 2: Atualiza com campos customizados e demais campos
            # CRÍTICO: Ordem importa! Campos customizados obrigatórios devem ser atualizados PRIMEIRO
            # para evitar erros de validação do Salesforce
            # NOTA: Mesmo se as atualizações falharem, retornamos o lead_id pois o lead básico foi criado
            
            # GRUPO 1: Campos customizados obrigatórios (PRIMEIRO!)
            required_custom = {}
            for field in ['Country', 'BU_Business_Unit__c', 'Estrat_gia__c', 'Tipo_de_Pessoa__c', 'ICP__c', 'Doc_CNPJ__c', 'CNPJ__c']:
                if field in lead_payload_final and lead_payload_final[field] is not None:
                    required_custom[field] = lead_payload_final[field]
            
            if required_custom:
                print(f"[SALESFORCE] [DEBUG] Atualizando campos customizados obrigatórios: {list(required_custom.keys())}")
                try:
                    sf.Lead.update(lead_id, required_custom)
                    print(f"[SALESFORCE] Campos customizados obrigatórios atualizados: [OK]")
                except Exception as e:
                    print(f"[SALESFORCE] [AVISO] Erro ao atualizar campos obrigatórios: {e}")
                    # Continua mesmo se falhar - lead já foi criado
            
            # GRUPO 2: Campos customizados específicos do setor Industrial
            # Doc_CNPJ__c só é incluído se foi coletado (não inclui se não foi coletado)
            industrial_custom = {}
            for field in ['Segmento_Industrial__c', 'Necessidade_Industrial__c', 
                         'Faturamento_Industrial__c']:
                if field in lead_payload_final and lead_payload_final[field] is not None:
                    industrial_custom[field] = lead_payload_final[field]
            
            # Adiciona Doc_CNPJ__c apenas se foi coletado (NUNCA adiciona CNPJ fictício)
            # Verifica explicitamente se o CNPJ foi coletado nos dados originais
            cnpj_coletado = cnpj_digits
            if cnpj_coletado and 'Doc_CNPJ__c' in lead_payload_final and lead_payload_final.get('Doc_CNPJ__c'):
                # Verifica se o CNPJ no payload não é o fictício
                cnpj_no_payload = lead_payload_final.get('Doc_CNPJ__c', '')
                if cnpj_no_payload and cnpj_no_payload != '00.000.000/0001-00' and cnpj_no_payload != '00000000000100':
                    industrial_custom['Doc_CNPJ__c'] = lead_payload_final['Doc_CNPJ__c']
                    print(f"[SALESFORCE] [DEBUG] Doc_CNPJ__c adicionado ao update (foi coletado): {lead_payload_final['Doc_CNPJ__c']}")
                else:
                    print(f"[SALESFORCE] [AVISO] Doc_CNPJ__c NÃO adicionado - CNPJ fictício detectado ou não coletado")
            else:
                print(f"[SALESFORCE] [DEBUG] Doc_CNPJ__c NÃO adicionado - CNPJ não foi coletado nos dados originais")
            
            if industrial_custom and lead_payload_final.get('BU_Business_Unit__c') == 'Industrial':
                print(f"[SALESFORCE] [DEBUG] Atualizando campos do setor Industrial: {list(industrial_custom.keys())}")
                try:
                    sf.Lead.update(lead_id, industrial_custom)
                    print(f"[SALESFORCE] Campos do setor Industrial atualizados: [OK]")
                except Exception as e:
                    print(f"[SALESFORCE] [AVISO] Erro ao atualizar campos do setor Industrial: {e}")
                    # Continua mesmo se falhar - lead já foi criado
            
            # GRUPO 3: Campos padrão (após os customizados obrigatórios)
            standard_fields = {}
            for field in ['FirstName', 'Title', 'Website', 'LeadSource', 'Status', 'Industry', 'Country']:
                if field in lead_payload_final and lead_payload_final[field] is not None:
                    standard_fields[field] = lead_payload_final[field]
            
            if standard_fields:
                print(f"[SALESFORCE] [DEBUG] Atualizando campos padrão: {list(standard_fields.keys())}")
                try:
                    sf.Lead.update(lead_id, standard_fields)
                    print(f"[SALESFORCE] Campos padrão atualizados: [OK]")
                except Exception as e:
                    print(f"[SALESFORCE] [AVISO] Erro ao atualizar campos padrão: {e}")
                    # Continua mesmo se falhar - lead já foi criado
            
            # CRÍTICO: Retorna o lead_id mesmo se algumas atualizações falharem
            # O lead básico foi criado com sucesso, então retornamos o ID
            print(f"[SALESFORCE] [OK] Lead criado e atualizado: {lead_id}")
            # Se chegou aqui, o lead foi criado com sucesso, então retorna o ID
            # Mesmo que algumas atualizações tenham falhado, o lead existe
            if lead_id:
                if summary:
                    try:
                        formatted_summary = _format_salesnotes_comment(lead_id, summary, ai_name="Eduardo")
                        update_payload = {'Coment_rio__c': formatted_summary}
                        sf.Lead.update(lead_id, update_payload)
                        print(f"[SALESFORCE] Resumo adicionado no campo Coment_rio__c (formato SalesNotes): [OK]")
                    except Exception as summary_error:
                        print(f"[SALESFORCE] [AVISO] Erro ao adicionar resumo formatado: {summary_error}")
                        try:
                            update_payload = {'Coment_rio__c': summary}
                            sf.Lead.update(lead_id, update_payload)
                            print(f"[SALESFORCE] Resumo adicionado sem formatação: [OK]")
                        except Exception:
                            pass
                return lead_id
        except Exception as create_error:
            # Se o lead_id já foi criado antes do erro, retorna ele
            if 'lead_id' in locals() and lead_id:
                print(f"[SALESFORCE] [AVISO] Erro durante atualizações, mas lead já foi criado: {lead_id}")
                if summary:
                    try:
                        formatted_summary = _format_salesnotes_comment(lead_id, summary, ai_name="Eduardo")
                        update_payload = {'Coment_rio__c': formatted_summary}
                        sf.Lead.update(lead_id, update_payload)
                    except Exception:
                        try:
                            update_payload = {'Coment_rio__c': summary}
                            sf.Lead.update(lead_id, update_payload)
                        except Exception:
                            pass
                return lead_id
            
            error_str = str(create_error).lower()
            # Se o erro for relacionado a campos de picklist inválidos, tenta sem esses campos
            if 'invalid_or_null_for_restricted_picklist' in error_str or 'valor incorreto para campo de lista' in error_str:
                print(f"[SALESFORCE] [AVISO] Erro com campos de picklist, tentando criar sem campos problemáticos...")
                lead_payload_fallback = lead_payload.copy()
                # Remove campos que podem ter valores inválidos
                lead_payload_fallback.pop('BU_Business_Unit__c', None)
                lead_payload_fallback.pop('Tipo_de_Pessoa__c', None)
                # Garante que os outros campos obrigatórios estão presentes
                if 'Country' not in lead_payload_fallback:
                    lead_payload_fallback['Country'] = 'Brasil'
                if 'Estrat_gia__c' not in lead_payload_fallback:
                    lead_payload_fallback['Estrat_gia__c'] = 'Inbound'
                try:
                    res = sf.Lead.create(lead_payload_fallback)
                    lead_id = res.get('id')
                    if lead_id:
                        print(f"[SALESFORCE] Lead criado com sucesso (sem campos de picklist problemáticos): {lead_id}")
                except Exception as fallback_error:
                    error_str_fallback = str(fallback_error).lower()
                    # Se ainda houver erro com Estrat_gia__c, tenta sem ele também
                    if 'estrat_gia__c' in error_str_fallback or 'estrategia' in error_str_fallback:
                        print(f"[SALESFORCE] [AVISO] Erro com Estrat_gia__c, tentando criar sem esse campo também...")
                        lead_payload_fallback2 = lead_payload_fallback.copy()
                        lead_payload_fallback2.pop('Estrat_gia__c', None)
                        try:
                            res = sf.Lead.create(lead_payload_fallback2)
                            lead_id = res.get('id')
                            if lead_id:
                                print(f"[SALESFORCE] Lead criado com sucesso (sem campos de picklist): {lead_id}")
                        except Exception as final_error:
                            print(f"[SALESFORCE] [ERRO] Erro mesmo sem campos de picklist: {final_error}")
                            raise create_error
                    else:
                        print(f"[SALESFORCE] [ERRO] Erro mesmo sem campos de picklist: {fallback_error}")
                        raise create_error
            else:
                raise create_error
    except Exception as create_error:
            # Se erro for de campo inválido, tenta criar sem campos customizados INVÁLIDOS
            # MAS mantém os campos obrigatórios customizados
            error_str = str(create_error).lower()
            if 'no such column' in error_str or 'invalid_field' in error_str or 'invalidfield' in error_str:
                print(f"[SALESFORCE] [AVISO] Campo customizado inválido detectado, tentando criar sem campos customizados inválidos...")
                
                # Remove campos customizados inválidos (que terminam com __c), EXCETO os obrigatórios
                required_custom_fields = ['BU_Business_Unit__c', 'Estrat_gia__c', 'Tipo_de_Pessoa__c']
                lead_payload_clean = {}
                
                # Mantém campos padrão
                standard_fields = ['FirstName', 'LastName', 'Company', 'Title', 'Email', 'Phone', 'MobilePhone', 
                                 'Website', 'Street', 'City', 'State', 'PostalCode', 'Country', 'LeadSource', 
                                 'Status', 'Industry', 'AnnualRevenue', 'NumberOfEmployees', 'Description', 'OwnerId']
                for k, v in lead_payload.items():
                    if k in standard_fields or k == 'OwnerId':
                        lead_payload_clean[k] = v
                    elif k in required_custom_fields:
                        # Mantém campos obrigatórios customizados
                        lead_payload_clean[k] = v
                    # Remove outros campos customizados (__c) que não são obrigatórios
                
                # Garante que os campos obrigatórios estão presentes
                if 'Country' not in lead_payload_clean or not lead_payload_clean.get('Country'):
                    lead_payload_clean['Country'] = 'Brasil'
                if 'BU_Business_Unit__c' not in lead_payload_clean or not lead_payload_clean.get('BU_Business_Unit__c'):
                    sector_low = (sector or data.get('sector', '')).lower()
                    if sector_low == 'industrial':
                        lead_payload_clean['BU_Business_Unit__c'] = 'Industrial'
                    elif sector_low == 'esportivo':
                        lead_payload_clean['BU_Business_Unit__c'] = 'Esportivo'
                    elif sector_low == 'cidades':
                        lead_payload_clean['BU_Business_Unit__c'] = 'Cidades'
                    elif sector_low == 'grow':
                        lead_payload_clean['BU_Business_Unit__c'] = 'Grow'
                    else:
                        lead_payload_clean['BU_Business_Unit__c'] = 'Industrial'
                if 'Estrat_gia__c' not in lead_payload_clean or not lead_payload_clean.get('Estrat_gia__c'):
                    lead_payload_clean['Estrat_gia__c'] = 'Inbound'
                if 'Tipo_de_Pessoa__c' not in lead_payload_clean or not lead_payload_clean.get('Tipo_de_Pessoa__c'):
                    lead_payload_clean['Tipo_de_Pessoa__c'] = 'Pessoa Jurídica (PJ)'
                
                # Não adiciona summary aqui, será formatado após criar o lead
                
                print(f"[SALESFORCE] Tentando criar lead com {len(lead_payload_clean)} campos (incluindo obrigatórios)...")
                try:
                    res = sf.Lead.create(lead_payload_clean)
                    lead_id = res.get('id')
                    if lead_id:
                        print(f"[SALESFORCE] [OK] Lead criado com sucesso (sem campos customizados): {lead_id}")
                        # Se houver summary, formata e adiciona no formato SalesNotes
                        if summary:
                            try:
                                formatted_summary = _format_salesnotes_comment(lead_id, summary, ai_name="Eduardo")
                                update_payload = {'Coment_rio__c': formatted_summary}
                                sf.Lead.update(lead_id, update_payload)
                                print(f"[SALESFORCE] Resumo adicionado no campo Coment_rio__c (formato SalesNotes): [OK]")
                            except Exception as summary_error:
                                print(f"[SALESFORCE] [AVISO] Erro ao adicionar resumo formatado: {summary_error}")
                                # Tenta adicionar sem formatação como fallback
                                try:
                                    update_payload = {'Coment_rio__c': summary}
                                    sf.Lead.update(lead_id, update_payload)
                                    print(f"[SALESFORCE] Resumo adicionado sem formatação: [OK]")
                                except Exception:
                                    pass
                        return lead_id
                except Exception as retry_error:
                    print(f"[SALESFORCE] [ERRO] Erro mesmo sem campos customizados: {retry_error}")
                    raise create_error  # Re-lança o erro original
            
            # Se não for erro de campo, re-lança
            raise create_error
        
    except Exception as e:
        # Log do erro para debug (pode ser removido em produção)
        import traceback
        print(f"[SALESFORCE] Erro ao criar lead no Salesforce: {e}")
        print(traceback.format_exc())
        return None


def create_person_and_deal_pipedrive(data: Dict[str, Any], owner_id: Optional[int] = None) -> Optional[int]:
    """
    Cria uma pessoa e um negócio no Pipedrive. Usa pipe_api token do .env.
    Retorna o ID do negócio (deal) ou None.
    """
    _load_env_once()
    token = os.getenv('PIPEDRIVE_API_TOKEN') or os.getenv('PIPE_API_TOKEN') or os.getenv('PIPEDRIVE_TOKEN') or os.getenv('PIPE_API') or os.getenv('pipe_api')
    base_url = os.getenv('PIPEDRIVE_BASE_URL', 'https://api.pipedrive.com').rstrip('/')
    if not token:
        return None

    try:
        # Resolve pipeline and stage IDs by name (defaults can be overridden via env)
        # Ajuste setorial: para 'cidades', usar Funil/Etapa específicos
        sector = str(data.get('sector') or '').strip().lower()
        if sector == 'cidades':
            pipeline_name = os.getenv('PIPEDRIVE_PIPELINE_NAME_CIDADES', 'Funil - Licitações Tipo 1')
            stage_name = os.getenv('PIPEDRIVE_STAGE_NAME_CIDADES', 'Agendado')
        else:
            pipeline_name = os.getenv('PIPEDRIVE_PIPELINE_NAME', 'pre licitação')
            stage_name = os.getenv('PIPEDRIVE_STAGE_NAME', 'entrada')

        def _resolve_ids() -> (Optional[int], Optional[int]):
            try:
                r_pipes = requests.get(f"{base_url}/v1/pipelines", params={'api_token': token}, timeout=30)
                r_pipes.raise_for_status()
                pipes = r_pipes.json().get('data') or []
                pipe_id = None
                for p in pipes:
                    # name field should exist
                    if str(p.get('name', '')).strip().lower() == pipeline_name.strip().lower():
                        pipe_id = p.get('id')
                        break
                stage_id = None
                if pipe_id:
                    r_stages = requests.get(
                        f"{base_url}/v1/stages",
                        params={'api_token': token, 'pipeline_id': pipe_id},
                        timeout=30
                    )
                    r_stages.raise_for_status()
                    stages = r_stages.json().get('data') or []
                    for s in stages:
                        if str(s.get('name', '')).strip().lower() == stage_name.strip().lower():
                            stage_id = s.get('id')
                            break
                return pipe_id, stage_id
            except Exception:
                return None, None

        pipe_id, stage_id = _resolve_ids()

        org_name = (
            data.get('company')
            or data.get('empresa')
            or data.get('orgao')
            or data.get('órgão')
            or data.get('entidade')
            or data.get('cidade')
            or data.get('city')
            or data.get('contact_name')
            or 'Organização WhatsApp'
        )
        org_id = None
        try:
            org_payload = {
                'name': org_name,
                'visible_to': 3
            }
            r_org = requests.post(
                f"{base_url}/v1/organizations",
                params={'api_token': token},
                json=org_payload,
                timeout=30
            )
            r_org.raise_for_status()
            org_id = (r_org.json().get('data') or {}).get('id')
        except Exception:
            org_id = None

        # Create person
        person_payload = {
            'name': data.get('contact_name') or data.get('company') or 'Contato WhatsApp',
            'email': data.get('email'),
            'phone': data.get('phone'),
            'visible_to': 3  # shared within company
        }
        if org_id:
            person_payload['org_id'] = org_id
        r_person = requests.post(f"{base_url}/v1/persons", params={'api_token': token}, json=person_payload, timeout=30)
        r_person.raise_for_status()
        person_id = (r_person.json().get('data') or {}).get('id')

        city_name = data.get('city') or data.get('cidade')
        if city_name and org_name and str(city_name).strip().lower() != str(org_name).strip().lower():
            lead_ref = f"{city_name}/{org_name}"
        else:
            lead_ref = org_name or city_name or (data.get('sector') or 'Lead')

        # Create deal
        deal_payload = {
            'title': f"LEAD SDR - {lead_ref}",
            'person_id': person_id,
            'value': data.get('value') or 0,
            'currency': data.get('currency') or 'BRL',
        }
        if pipe_id:
            deal_payload['pipeline_id'] = pipe_id
        if stage_id:
            deal_payload['stage_id'] = stage_id
        if owner_id:
            deal_payload['user_id'] = owner_id
        r_deal = requests.post(f"{base_url}/v1/deals", params={'api_token': token}, json=deal_payload, timeout=30)
        r_deal.raise_for_status()
        deal_id = (r_deal.json().get('data') or {}).get('id')

        summary = data.get('summary') or data.get('conversation_summary') or data.get('resumo')
        if summary and deal_id:
            try:
                if not isinstance(summary, str):
                    summary = json.dumps(summary, ensure_ascii=False)
                note_payload = {
                    'content': summary,
                    'deal_id': deal_id,
                    'person_id': person_id,
                }
                if org_id:
                    note_payload['org_id'] = org_id
                r_note = requests.post(
                    f"{base_url}/v1/notes",
                    params={'api_token': token},
                    json=note_payload,
                    timeout=30
                )
                r_note.raise_for_status()
                print(f"[PIPEDRIVE] [OK] Nota adicionada ao deal {deal_id} com sucesso")
            except Exception as note_error:
                print(f"[PIPEDRIVE] [ERRO] Erro ao adicionar nota ao deal {deal_id}: {note_error}")
                import traceback
                traceback.print_exc()
        return deal_id
    except Exception:
        return None


def _format_salesnotes_comment(lead_id: str, comment_text: str, ai_name: str = "Eduardo") -> str:
    """
    Formata um comentário no formato esperado pelo componente SalesNotes do Salesforce.
    Formato: [DD/MM/YYYY HH:MM - Nome do Usuário | RecordId | Nome do Registro]
    """
    from datetime import datetime
    
    _load_env_once()
    username = os.getenv('SALESFORCE_USERNAME')
    password = os.getenv('SALESFORCE_PASSWORD')
    token = os.getenv('SALESFORCE_SECURITY_TOKEN', '')
    domain = 'login'
    api_version = os.getenv('SALESFORCE_API_VERSION', '59.0')
    
    # Obtém o nome do registro
    record_name = "Lead"
    try:
        if username and password:
            sf = Salesforce(username=username, password=password, security_token=token, domain=domain, version=api_version)
            # Usa query SOQL para buscar Name e Company
            query_result = sf.query(f"SELECT Name, Company FROM Lead WHERE Id = '{lead_id}' LIMIT 1")
            if query_result and query_result.get('records'):
                lead_info = query_result['records'][0]
                # Formata o nome: "Nome - Empresa" ou apenas "Nome" ou "Empresa"
                name = lead_info.get('Name', '')
                company = lead_info.get('Company', '')
                if name and company:
                    record_name = f"{name} - {company}"
                elif name:
                    record_name = name
                elif company:
                    record_name = company
    except Exception as e:
        print(f"[SALESFORCE] [AVISO] Não foi possível obter o nome do lead {lead_id}: {e}")
        record_name = "Lead"
    
    # Formata data/hora atual
    now = datetime.now()
    timestamp = now.strftime('%d/%m/%Y %H:%M')
    
    # Formata o comentário no padrão SalesNotes
    formatted_comment = f"[{timestamp} - {ai_name} | {lead_id} | {record_name}]\n{comment_text}"
    
    return formatted_comment


def _append_salesnotes_comment(existing_comments: str, new_comment: str) -> str:
    """
    Adiciona um novo comentário ao campo Coment_rio__c existente.
    Se o campo estiver vazio, retorna apenas o novo comentário.
    Se já houver comentários, adiciona uma linha em branco e o novo comentário.
    """
    if not existing_comments or existing_comments.strip() == "":
        return new_comment
    
    # Adiciona o novo comentário após os existentes
    return f"{existing_comments}\n\n{new_comment}"


def update_lead_comment(lead_id: str, comment: str, ai_name: str = "Eduardo") -> bool:
    """
    Atualiza o campo customizado Coment_rio__c de um Lead existente no Salesforce.
    O comentário é formatado no padrão SalesNotes: [DD/MM/YYYY HH:MM - Nome | RecordId | Nome do Registro]
    Requer: SALESFORCE_USERNAME, SALESFORCE_PASSWORD, SALESFORCE_SECURITY_TOKEN
    Retorna True se sucesso, False caso contrário.
    """
    _load_env_once()
    username = os.getenv('SALESFORCE_USERNAME')
    password = os.getenv('SALESFORCE_PASSWORD')
    token = os.getenv('SALESFORCE_SECURITY_TOKEN', '')
    domain = 'login'
    api_version = os.getenv('SALESFORCE_API_VERSION', '59.0')

    if not username or not password or not lead_id or not comment:
        print(f"[SALESFORCE] [ERRO] Parâmetros faltando: username={bool(username)}, password={bool(password)}, lead_id={lead_id}, comment={bool(comment)}")
        return False

    try:
        sf = Salesforce(username=username, password=password, security_token=token, domain=domain, version=api_version)
        
        # Obtém os comentários existentes
        try:
            # Usa query SOQL para buscar o campo Coment_rio__c
            query_result = sf.query(f"SELECT Coment_rio__c FROM Lead WHERE Id = '{lead_id}' LIMIT 1")
            if query_result and query_result.get('records'):
                existing_comments = query_result['records'][0].get('Coment_rio__c', '') or ''
            else:
                existing_comments = ''
        except Exception as e:
            print(f"[SALESFORCE] [AVISO] Não foi possível ler comentários existentes: {e}")
            existing_comments = ''
        
        # Formata o novo comentário no padrão SalesNotes
        formatted_comment = _format_salesnotes_comment(lead_id, comment, ai_name)
        
        # Adiciona ao comentário existente
        final_comments = _append_salesnotes_comment(existing_comments, formatted_comment)
        
        # Atualiza o campo Coment_rio__c
        update_payload = {
            'Coment_rio__c': final_comments
        }
        
        result = sf.Lead.update(lead_id, update_payload)
        
        if result:
            print(f"[SALESFORCE] [OK] Campo Coment_rio__c atualizado com sucesso no lead {lead_id}")
            print(f"[SALESFORCE] Comentário adicionado: {formatted_comment[:100]}...")
            return True
        else:
            print(f"[SALESFORCE] [ERRO] Falha ao atualizar lead {lead_id}")
            return False
            
    except Exception as e:
        import traceback
        print(f"[SALESFORCE] [ERRO] Erro ao atualizar comentário no lead {lead_id}: {e}")
        print(traceback.format_exc())
        return False
