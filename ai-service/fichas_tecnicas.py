"""
Sistema de busca em fichas técnicas e documentos técnicos da pasta data/
Processa PDFs e permite busca semântica de informações técnicas
"""
import os
import json
import re
from pathlib import Path
from typing import Dict, List, Any, Optional
import threading

# Cache para documentos processados
_documents_cache: Dict[str, Any] = {
    "loaded": False,
    "documents": [],
    "lock": threading.Lock()
}

# Caminho da pasta data (na raiz do projeto)
DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _safe_lower(s: Optional[str]) -> str:
    """Normaliza string para lowercase"""
    return (s or "").strip().lower()


def _extract_text_from_pdf(pdf_path: Path) -> Optional[str]:
    """Extrai texto de um arquivo PDF"""
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                try:
                    page_text = page.extract_text()
                    if page_text:
                        text_parts.append(page_text)
                except Exception as page_error:
                    # Ignora erros de página individual e continua
                    continue
        if text_parts:
            return "\n\n".join(text_parts)
        return None
    except ImportError:
        # Fallback para PyPDF2 se pdfplumber não estiver disponível
        try:
            import PyPDF2
            import warnings
            # Suprime avisos do PyPDF2 sobre cores inválidas
            warnings.filterwarnings('ignore')
            text_parts = []
            with open(pdf_path, 'rb') as file:
                pdf_reader = PyPDF2.PdfReader(file)
                for page in pdf_reader.pages:
                    try:
                        text = page.extract_text()
                        if text:
                            text_parts.append(text)
                    except Exception:
                        # Ignora erros de página individual
                        continue
            if text_parts:
                return "\n\n".join(text_parts)
            return None
        except Exception as e:
            print(f"[FICHAS] Erro ao extrair texto de {pdf_path.name}: {e}")
            return None
    except Exception as e:
        print(f"[FICHAS] Erro ao processar PDF {pdf_path.name}: {e}")
        return None


def _load_documents() -> List[Dict[str, Any]]:
    """Carrega todos os documentos da pasta data/"""
    documents = []
    
    if not DATA_DIR.exists():
        print(f"[FICHAS] Pasta data não encontrada: {DATA_DIR}")
        return documents
    
    # Busca todos os PDFs na pasta data
    pdf_files = list(DATA_DIR.glob("*.pdf"))
    
    if not pdf_files:
        print(f"[FICHAS] Nenhum PDF encontrado em {DATA_DIR}")
        return documents
    
    print(f"[FICHAS] Encontrados {len(pdf_files)} arquivos PDF em {DATA_DIR}")
    
    for pdf_path in pdf_files:
        print(f"[FICHAS] Processando: {pdf_path.name}")
        text = _extract_text_from_pdf(pdf_path)
        
        if text:
            # Limpa e normaliza o texto
            text = re.sub(r'\s+', ' ', text).strip()
            
            # Extrai título do nome do arquivo
            title = pdf_path.stem.replace('_', ' ').replace('-', ' ')
            
            # Detecta tipo de documento
            doc_type = "ficha_tecnica"
            if "apresentação" in _safe_lower(title) or "apresentacao" in _safe_lower(title):
                doc_type = "apresentacao"
            elif "ficha" in _safe_lower(title) or "técnica" in _safe_lower(title) or "tecnica" in _safe_lower(title):
                doc_type = "ficha_tecnica"
            
            # Divide o texto em chunks menores para busca mais precisa
            # Chunks de aproximadamente 500 palavras
            words = text.split()
            chunks = []
            chunk_size = 500
            for i in range(0, len(words), chunk_size):
                chunk_text = ' '.join(words[i:i + chunk_size])
                chunks.append(chunk_text)
            
            document = {
                "file_name": pdf_path.name,
                "title": title,
                "type": doc_type,
                "full_text": text,
                "chunks": chunks,
                "word_count": len(words)
            }
            
            documents.append(document)
            print(f"[FICHAS] [OK] Processado: {pdf_path.name} ({len(words)} palavras, {len(chunks)} chunks)")
        else:
            print(f"[FICHAS] [AVISO] Nao foi possivel extrair texto de {pdf_path.name}")
    
    return documents


def _score_chunk(query: str, chunk: str, document: Dict[str, Any]) -> float:
    """Calcula score de relevância de um chunk para a query"""
    query_lower = _safe_lower(query)
    chunk_lower = _safe_lower(chunk)
    title_lower = _safe_lower(document.get("title", ""))
    
    score = 0.0
    
    # Conta ocorrências de palavras da query no chunk
    query_words = re.split(r'\W+', query_lower)
    for word in query_words:
        if len(word) > 2:  # Ignora palavras muito curtas
            # Score maior se palavra aparece no título
            if word in title_lower:
                score += 3.0
            # Score médio se aparece no chunk
            count = chunk_lower.count(word)
            score += count * 1.0
    
    # Bonus se a query completa aparece no chunk
    if query_lower in chunk_lower:
        score += 5.0
    
    # Normaliza pelo tamanho do chunk (evita bias para chunks muito longos)
    chunk_length = len(chunk.split())
    if chunk_length > 0:
        score = score / (1 + chunk_length / 100)
    
    return score


def _search_documents(query: str, top_k: int = 3) -> List[Dict[str, Any]]:
    """Busca nos documentos carregados"""
    with _documents_cache["lock"]:
        if not _documents_cache["loaded"]:
            print("[FICHAS] Carregando documentos pela primeira vez...")
            _documents_cache["documents"] = _load_documents()
            _documents_cache["loaded"] = True
        
        documents = _documents_cache["documents"]
    
    if not documents:
        return []
    
    # Busca em todos os chunks de todos os documentos
    scored_results = []
    
    for doc in documents:
        for chunk_idx, chunk in enumerate(doc.get("chunks", [])):
            score = _score_chunk(query, chunk, doc)
            if score > 0:
                scored_results.append({
                    "score": score,
                    "document": doc,
                    "chunk_index": chunk_idx,
                    "chunk_text": chunk
                })
    
    # Ordena por score e retorna top_k
    scored_results.sort(key=lambda x: x["score"], reverse=True)
    
    # Agrupa por documento para evitar múltiplos resultados do mesmo documento
    seen_docs = set()
    unique_results = []
    for result in scored_results:
        doc_name = result["document"]["file_name"]
        if doc_name not in seen_docs or len(unique_results) < top_k:
            unique_results.append(result)
            seen_docs.add(doc_name)
            if len(unique_results) >= top_k:
                break
    
    return unique_results


def query_fichas_tecnicas(query: str, top_k: int = 3) -> Dict[str, Any]:
    """
    Busca informações técnicas nos documentos da pasta data/
    
    Args:
        query: Texto da busca
        top_k: Número máximo de resultados
    
    Returns:
        Dicionário com resultados da busca
    """
    if not query or not query.strip():
        return {
            "fichas_tecnicas_results": [],
            "total_documents": 0,
            "query": query
        }
    
    results = _search_documents(query.strip(), top_k)
    
    # Formata resultados para o formato esperado
    formatted_results = []
    for result in results:
        doc = result["document"]
        chunk_text = result["chunk_text"]
        
        # Limita tamanho do chunk para resposta
        if len(chunk_text) > 1000:
            chunk_text = chunk_text[:1000] + "..."
        
        formatted_results.append({
            "file_name": doc["file_name"],
            "title": doc["title"],
            "type": doc["type"],
            "excerpt": chunk_text,
            "score": round(result["score"], 2)
        })
    
    with _documents_cache["lock"]:
        total_docs = len(_documents_cache["documents"])
    
    return {
        "fichas_tecnicas_results": formatted_results,
        "total_documents": total_docs,
        "query": query
    }


def reload_documents() -> bool:
    """Recarrega os documentos da pasta data/ (útil após adicionar novos arquivos)"""
    with _documents_cache["lock"]:
        _documents_cache["loaded"] = False
        _documents_cache["documents"] = []
    
    # Força recarregamento na próxima busca
    _load_documents()
    return True


if __name__ == "__main__":
    # Teste rápido
    print("=== TESTE DE BUSCA EM FICHAS TÉCNICAS ===\n")
    
    test_queries = [
        "SUNNA",
        "eficiencia",
        "potência",
        "espectro",
        "IP66"
    ]
    
    for q in test_queries:
        print(f"\n🔍 Busca: '{q}'")
        result = query_fichas_tecnicas(q, top_k=2)
        print(f"   Documentos encontrados: {result['total_documents']}")
        print(f"   Resultados: {len(result['fichas_tecnicas_results'])}")
        for r in result['fichas_tecnicas_results']:
            print(f"   - {r['title']} (score: {r['score']})")
            print(f"     {r['excerpt'][:100]}...")
