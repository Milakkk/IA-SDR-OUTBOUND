"""
Sistema de extração automática de dados da conversa
"""
import json
from typing import Dict, Any, List, Optional
import deepseek_client

def extract_lead_data(conversation_history: List[Dict[str, str]], contact_id: str) -> Dict[str, Any]:
    """
    Extrai dados do lead analisando o histórico completo da conversa.
    
    Args:
        conversation_history: Histórico completo da conversa
        contact_id: ID do contato para contexto
    
    Returns:
        Dicionário com dados extraídos
    """
    # Monta o texto completo da conversa
    conversation_text = "\n".join([
        f"{msg['role'].upper()}: {msg['content']}"
        for msg in conversation_history
        if msg['role'] != 'system'
    ])
    
    extraction_prompt = f"""Analise a seguinte conversa e extraia TODOS os dados mencionados pelo usuário.
Retorne APENAS um JSON válido, sem texto adicional, com os seguintes campos:

{{
    "nome": "nome completo ou primeiro nome mencionado pelo usuário",
    "email": "email completo se mencionado (ex: joao@teste.com)",
    "telefone": "telefone se mencionado (ou use o contact_id: {contact_id})",
    "cidade": "cidade mencionada",
    "estado": "estado/UF se mencionado (ex: PR, SP, SC)",
    "setor": "industrial, esportivo, cidades, grow ou null. Use 'industrial' para barracão, galpão, fábrica, pátio, estacionamento, empresa privada. Use 'esportivo' para estádio, quadra, ginásio, arena. Use 'cidades' para iluminação pública, via pública, licitação, prefeitura, município, órgão público, CRC, CRCs, CRP, CREA, CRM, OAB, conselho regional, autarquia, fundação pública, empresa pública, governo, secretaria. Use 'grow' para estufa, casa de vegetação, cultivo.",
    "estrutura": "para grow: descreva se tem estufa/casa de vegetação (sim/não + detalhes se houver)",
    "cultivo": "para grow: qual cultivo o usuário mencionou (ex: eucalipto, pinus, café, cana, microverde, hortaliças) ou null",
    "muda_sem_cultivo": "para grow: se precisa ser muda (sem cultivo) (sim/não) ou null",
    "tipo_local": "tipo de local (barracão, fábrica, quadra, estádio, etc)",
    "area": "área em m² se mencionada (apenas o número, sem 'm²')",
    "necessidade": "necessidade específica mencionada",
    "publico_privado": "público, privado ou null. Se mencionar 'empresa privada', 'privado', 'particular', extraia 'privado'. Se mencionar 'prefeitura', 'município', 'público', extraia 'público'",
    "tipo_cliente": "público, privado ou null (mesma lógica de publico_privado)",
    "projeto_luminotecnico": "sim, não ou null. Se mencionar 'já tem projeto', 'tem projeto', 'projeto aprovado', extraia 'sim'. Se mencionar 'não tem', 'ainda não', extraia 'não'",
    "segmento": "segmento se mencionado (ex: manufatura, alimentício, têxtil, etc)",
    "segmento_empresa": "segmento da empresa se mencionado (mesma lógica de segmento)",
    "tipo_lugar": "tipo de lugar/instalação se mencionado (ex: barracão, galpão, fábrica, estádio, quadra, etc)",
    "tipo_local": "tipo de local se mencionado (mesma lógica de tipo_lugar)",
    "tamanho": "tamanho/área se mencionado (ex: 5000, 3000m², etc)",
    "produto": "produto de interesse se mencionado (ex: LED, Solar, BESS, Bateria)",
    "empresa": "nome da empresa se mencionado",
    "localizacao": "localização/município se mencionado (para setor cidades)"
}}

IMPORTANTE:
- Se o usuário disse "Meu nome é João Silva", extraia "nome": "João Silva"
- Se o usuário disse "joao.silva@teste.com", extraia "email": "joao.silva@teste.com"
- Se o usuário disse "barracão", "galpão", "fábrica", "pátio", "estacionamento", extraia "setor": "industrial"
- Se o usuário disse "3000m²" ou "3000 m²", extraia "area": "3000"
- Se o usuário disse "tenho uma fabrica de motores, motores milak", extraia "empresa": "Motores Milak" e "setor": "industrial"
- Se o usuário disse "sou da prefeitura de curitiba", extraia "empresa": "Prefeitura de Curitiba" e "setor": "cidades"
- Se o usuário mencionar siglas como "CRC", "CRCs", "CRCPR", "CRP", "CREA", "CRM", "OAB", "conselho", "órgão público", extraia "setor": "cidades" (mesmo que seja para usina solar)
- Se o usuário mencionar "crcpr" ou "crc pr", extraia "setor": "cidades" e "empresa": "CRC PR" ou similar
- Para grow, se mencionar "estufa" ou "casa de vegetação", extraia em "estrutura"
- Para grow, se mencionar o tipo de cultivo, extraia em "cultivo"
- Para grow, se mencionar "muda" / "sem cultivo", extraia em "muda_sem_cultivo"
- Se algum campo não foi mencionado, use null (não use string vazia)

Conversa:
{conversation_text}

JSON:"""

    messages = [
        {"role": "system", "content": "Você é um assistente que extrai dados estruturados de conversas. Retorne APENAS JSON válido, sem texto adicional."},
        {"role": "user", "content": extraction_prompt}
    ]
    
    try:
        print(f"[EXTRACT] Extraindo dados para contact_id={contact_id}")
        response = deepseek_client.chat_completion(messages, temperature=0.3, max_tokens=500)
        
        # Tenta parsear JSON da resposta
        response = response.strip()
        
        # Remove markdown code blocks se houver
        if response.startswith("```"):
            lines = response.split("\n")
            response = "\n".join(lines[1:-1]) if len(lines) > 2 else response
        if response.startswith("```json"):
            lines = response.split("\n")
            response = "\n".join(lines[1:-1]) if len(lines) > 2 else response
        
        data = json.loads(response)
        
        # Adiciona contact_id como telefone se não tiver telefone
        if not data.get("telefone") and contact_id:
            data["telefone"] = contact_id
        
        print(f"[EXTRACT] Dados extraídos: {list(data.keys())}")
        return data
        
    except json.JSONDecodeError as e:
        print(f"[EXTRACT] Erro ao parsear JSON: {e}")
        print(f"[EXTRACT] Resposta recebida: {response[:200]}")
        return {}
    except Exception as e:
        print(f"[EXTRACT] Erro na extração: {e}")
        return {}


def should_save_lead(data: Dict[str, Any], sector: Optional[str] = None) -> bool:
    """
    Determina se os dados coletados são suficientes para salvar o lead.
    
    Args:
        data: Dados extraídos
        sector: Setor do lead (se conhecido)
    
    Returns:
        True se dados estão completos o suficiente
    """
    # Campos obrigatórios mínimos
    required_fields = ["nome", "cidade"]
    
    # Verifica campos obrigatórios
    for field in required_fields:
        if not data.get(field):
            return False
    
    # Para setores específicos, verifica campos adicionais
    if sector == "cidades":
        if not data.get("empresa"):
            return False
    
    return True


def get_missing_fields(data: Dict[str, Any], sector: Optional[str] = None) -> List[str]:
    """
    Retorna lista de campos que ainda faltam coletar.
    
    Args:
        data: Dados extraídos
        sector: Setor do lead
    
    Returns:
        Lista de nomes de campos faltantes
    """
    missing = []
    
    required = ["nome", "cidade"]
    for field in required:
        if not data.get(field):
            missing.append(field)
    
    if sector == "cidades":
        if not data.get("empresa"):
            missing.append("empresa")
    
    return missing

