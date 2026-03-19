"""
Script para processar CSVs da Receita Federal e extrair contatos com telefone válido
para envio de mensagens outbound via WhatsApp.
"""
import csv
import json
import os
import re
from pathlib import Path
from typing import List, Dict, Optional

def normalize_phone(phone: str) -> Optional[str]:
    """
    Normaliza número de telefone para formato WhatsApp (55XXXXXXXXXXX).
    Remove caracteres não numéricos e valida formato brasileiro.
    """
    if not phone or phone.strip() == '':
        return None
    
    # Remove todos os caracteres não numéricos
    digits = re.sub(r'\D', '', phone)
    
    # Remove zeros à esquerda
    digits = digits.lstrip('0')
    
    # Validações
    if not digits:
        return None
    
    # Se começa com 55, já está no formato correto
    if digits.startswith('55'):
        # Verifica se tem pelo menos 12 dígitos (55 + DDD + número)
        if len(digits) >= 12 and len(digits) <= 13:
            return digits
        return None
    
    # Se não começa com 55, adiciona
    # Verifica se tem formato de celular brasileiro (10 ou 11 dígitos sem DDD)
    if len(digits) == 10 or len(digits) == 11:
        # Assume DDD 11 se não tiver
        if len(digits) == 10:
            # Formato: DDD + 8 dígitos (fixo) ou DDD + 9 dígitos (celular antigo)
            return '55' + digits
        elif len(digits) == 11:
            # Formato: DDD + 9 dígitos (celular)
            return '55' + digits
        return None
    
    # Se tem 12 ou 13 dígitos sem o 55, adiciona
    if len(digits) == 12 or len(digits) == 13:
        return '55' + digits
    
    return None

def is_valid_phone(phone: str) -> bool:
    """Verifica se o telefone é válido (celular brasileiro)."""
    normalized = normalize_phone(phone)
    if not normalized:
        return False
    
    # Remove o 55 inicial
    without_country = normalized[2:]
    
    # Verifica se tem DDD válido (11-99)
    ddd = without_country[:2]
    if not ddd.isdigit() or int(ddd) < 11 or int(ddd) > 99:
        return False
    
    # Verifica se o número tem 9 dígitos (celular) ou 8 dígitos (fixo)
    number = without_country[2:]
    if len(number) == 9:
        # Celular: deve começar com 9
        return number[0] == '9'
    elif len(number) == 8:
        # Fixo: aceita qualquer dígito inicial
        return True
    
    return False

def process_csv_file(csv_path: Path) -> List[Dict[str, any]]:
    """
    Processa um arquivo CSV e retorna lista de contatos válidos.
    """
    contacts = []
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            # Detecta delimitador
            sample = f.read(1024)
            f.seek(0)
            sniffer = csv.Sniffer()
            delimiter = sniffer.sniff(sample).delimiter
            
            reader = csv.DictReader(f, delimiter=delimiter)
            
            for row_num, row in enumerate(reader, start=2):  # Começa em 2 (linha 1 é header)
                # Tenta encontrar coluna de telefone
                phone = None
                phone_col = None
                
                # Procura por colunas que possam conter telefone
                for col in ['telefone', 'phone', 'celular', 'whatsapp', 'tel']:
                    if col in row and row[col]:
                        phone = row[col]
                        phone_col = col
                        break
                
                if not phone:
                    continue
                
                # Normaliza telefone
                normalized = normalize_phone(phone)
                if not normalized or not is_valid_phone(phone):
                    continue
                
                # Extrai informações da empresa
                contact = {
                    'phone': normalized,
                    'phone_raw': phone,
                    'cnpj': row.get('cnpj', '').strip(),
                    'razao_social': row.get('razao_social', '').strip(),
                    'nome_fantasia': row.get('nome_fantasia', '').strip(),
                    'cnae_principal': row.get('cnae_principal', '').strip(),
                    'uf': row.get('uf', '').strip(),
                    'municipio': row.get('municipio', '').strip(),
                    'cep': row.get('cep', '').strip(),
                    'logradouro': row.get('logradouro', '').strip(),
                    'numero': row.get('numero', '').strip(),
                    'bairro': row.get('bairro', '').strip(),
                    'email': row.get('email', '').strip(),
                    'source_file': str(csv_path.name),
                    'source_row': row_num
                }
                
                contacts.append(contact)
    
    except Exception as e:
        print(f"Erro ao processar {csv_path}: {e}")
        return []
    
    return contacts

def main():
    """Processa todos os CSVs no diretório atual e gera lista de contatos."""
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    
    # Procura CSVs no diretório raiz do projeto
    csv_files = [
        project_root / 'leads_por_cnae.csv',
        project_root / 'leads_por_nome.csv',
        project_root / 'leads_por_nome_estrito.csv'
    ]
    
    # Adiciona qualquer outro CSV que comece com 'leads_'
    for csv_file in project_root.glob('leads_*.csv'):
        if csv_file not in csv_files:
            csv_files.append(csv_file)
    
    all_contacts = []
    seen_phones = set()
    
    print("=" * 80)
    print("PROCESSAMENTO DE CSVs DA RECEITA FEDERAL")
    print("=" * 80)
    print()
    
    for csv_file in csv_files:
        if not csv_file.exists():
            print(f"⚠️  Arquivo não encontrado: {csv_file.name}")
            continue
        
        print(f"📄 Processando: {csv_file.name}")
        contacts = process_csv_file(csv_file)
        
        # Remove duplicatas por telefone
        unique_contacts = []
        for contact in contacts:
            phone = contact['phone']
            if phone not in seen_phones:
                seen_phones.add(phone)
                unique_contacts.append(contact)
        
        print(f"   ✅ {len(unique_contacts)} contatos válidos encontrados")
        all_contacts.extend(unique_contacts)
    
    print()
    print("=" * 80)
    print(f"📊 TOTAL: {len(all_contacts)} contatos únicos com telefone válido")
    print("=" * 80)
    print()
    
    # Cria diretório de saída
    output_dir = project_root / 'outbound_data'
    output_dir.mkdir(exist_ok=True)
    
    # Salva contatos processados
    output_file = output_dir / 'contacts_processed.json'
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump({
            'total_contacts': len(all_contacts),
            'processed_at': str(Path(__file__).stat().st_mtime),
            'contacts': all_contacts
        }, f, ensure_ascii=False, indent=2)
    
    print(f"✅ Contatos salvos em: {output_file}")
    print()
    
    # Estatísticas
    if all_contacts:
        estados = {}
        for contact in all_contacts:
            uf = contact.get('uf', 'N/A')
            estados[uf] = estados.get(uf, 0) + 1
        
        print("📈 Distribuição por Estado:")
        for uf, count in sorted(estados.items(), key=lambda x: x[1], reverse=True):
            print(f"   {uf}: {count}")
        print()
    
    return all_contacts

if __name__ == '__main__':
    main()



