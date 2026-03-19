"""
Script para criar leads perdidos no Salesforce
Verifica follow-ups marcados como 'lost' e cria leads no Salesforce com status 'Lost'
"""
import json
import os
import sys
from pathlib import Path
from datetime import datetime

# Adiciona o diretório ai-service ao path para importar crm
sys.path.insert(0, str(Path(__file__).parent.parent / 'ai-service'))

from crm import create_lead_salesforce

def main():
    """Cria leads perdidos no Salesforce."""
    project_root = Path(__file__).parent.parent
    followUpsFile = project_root / 'outbound_data' / 'follow_ups.json'
    
    if not followUpsFile.exists():
        print('⚠️  Arquivo de follow-ups não encontrado.')
        return
    
    try:
        with open(followUpsFile, 'r', encoding='utf-8') as f:
            followUps = json.load(f)
    except Exception as e:
        print(f'❌ Erro ao ler arquivo de follow-ups: {e}')
        return
    
    print('=' * 80)
    print('CRIANDO LEADS PERDIDOS NO SALESFORCE')
    print('=' * 80)
    print()
    
    created_count = 0
    error_count = 0
    
    for phone, followUp in followUps.items():
        if followUp.get('status') != 'lost':
            continue
        
        # Verifica se já foi criado
        if followUp.get('salesforce_lead_id'):
            print(f'⏭️  {followUp.get("empresa", "Empresa")} já tem lead criado: {followUp["salesforce_lead_id"]}')
            continue
        
        print(f'📝 Criando lead perdido para {followUp.get("empresa", "Empresa")}...')
        
        # Prepara dados do lead
        lead_data = {
            'company': followUp.get('empresa', 'Empresa'),
            'contact_name': followUp.get('contato', 'Contato'),
            'cargo': followUp.get('cargo', ''),
            'phone': phone,
            'source': 'WhatsApp Outbound',
            'status': 'Lost',
            'sector': 'grow',
            'city': '',
            'state': '',
            'email': ''
        }
        
        # Adiciona informações sobre os follow-ups na descrição
        description_parts = [
            f"Lead criado automaticamente após follow-ups sem resposta.",
            f"Empresa: {followUp.get('empresa', 'N/A')}",
            f"Contato: {followUp.get('contato', 'N/A')}",
            f"Telefone: {phone}",
            f"Mensagem inicial enviada em: {followUp.get('sent_at', 'N/A')}",
        ]
        
        if followUp.get('follow_ups'):
            for i, fu in enumerate(followUp.get('follow_ups', []), 1):
                if fu.get('sent'):
                    description_parts.append(f"Follow-up {i} enviado em: {fu.get('sent_at', 'N/A')}")
        
        description_parts.append(f"Marcado como perdido em: {followUp.get('marked_lost_at', datetime.now().isoformat())}")
        
        lead_data['description'] = '\n'.join(description_parts)
        
        owner_id = os.getenv('SALESFORCE_OWNER_ID', '')

        try:
            lead_id = create_lead_salesforce(
                data=lead_data,
                owner_id=owner_id,
                sector='grow',
                summary=f"Lead outbound sem resposta após follow-ups. Empresa: {followUp.get('empresa')}"
            )
            
            if lead_id:
                # Atualiza follow-up com ID do lead
                followUp['salesforce_lead_id'] = lead_id
                followUp['salesforce_created_at'] = datetime.now().isoformat()
                
                # Salva atualização
                with open(followUpsFile, 'w', encoding='utf-8') as f:
                    json.dump(followUps, f, ensure_ascii=False, indent=2)
                
                print(f'   ✅ Lead criado: {lead_id}')
                created_count += 1
            else:
                print(f'   ❌ Falha ao criar lead')
                error_count += 1
                
        except Exception as e:
            print(f'   ❌ Erro: {e}')
            error_count += 1
    
    print()
    print('=' * 80)
    print('📊 RESUMO')
    print('=' * 80)
    print(f'✅ Leads criados: {created_count}')
    print(f'❌ Erros: {error_count}')
    print('=' * 80)

if __name__ == '__main__':
    main()

