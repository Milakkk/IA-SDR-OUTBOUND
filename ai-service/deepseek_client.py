"""
Cliente para integração com DeepSeek API
"""
import os
import json
import time
from typing import List, Dict, Any, Optional
import requests
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=str(Path(__file__).resolve().parents[1] / '.env'))

DEEPSEEK_API_URL = os.getenv("DEEPSEEK_API_URL", "https://api.deepseek.com")
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

def chat_completion(
    messages: List[Dict[str, str]],
    temperature: float = 0.2,
    max_tokens: Optional[int] = 512,
    top_p: float = 0.9,
    stream: bool = True,
    timeout: int = 60
) -> str:
    """
    Envia mensagens para DeepSeek API e retorna a resposta.
    
    Args:
        messages: Lista de mensagens no formato [{"role": "system/user/assistant", "content": "..."}]
        temperature: Temperatura para geração (0.0 a 1.0) - padrão 0.2
        max_tokens: Número máximo de tokens na resposta - padrão 512
        top_p: Top-p sampling - padrão 0.9
        stream: Se deve usar streaming - padrão True
        timeout: Timeout em segundos
    
    Returns:
        Resposta do modelo como string
    """
    if not DEEPSEEK_API_KEY:
        error_msg = "DEEPSEEK_API_KEY não configurada no .env"
        print(f"[DEEPSEEK] [ERRO] ERRO: {error_msg}")
        raise ValueError(error_msg)
    
    # Validação básica da API key
    if len(DEEPSEEK_API_KEY) < 10:
        error_msg = f"DEEPSEEK_API_KEY parece inválida (muito curta: {len(DEEPSEEK_API_KEY)} caracteres)"
        print(f"[DEEPSEEK] [ERRO] ERRO: {error_msg}")
        raise ValueError(error_msg)
    
    url = f"{DEEPSEEK_API_URL}/v1/chat/completions"
    
    print(f"[DEEPSEEK] Configuração:")
    print(f"[DEEPSEEK]   URL: {url}")
    print(f"[DEEPSEEK]   Model: {DEEPSEEK_MODEL}")
    print(f"[DEEPSEEK]   API Key: {'*' * 10 + DEEPSEEK_API_KEY[-4:] if DEEPSEEK_API_KEY else 'NÃO CONFIGURADA'}")
    print(f"[DEEPSEEK]   Stream: {stream}")
    print(f"[DEEPSEEK]   Messages count: {len(messages)}")
    
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {DEEPSEEK_API_KEY}"
    }
    
    payload = {
        "model": DEEPSEEK_MODEL,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "top_p": top_p,
        "stream": stream
    }
    
    # Retry logic
    max_retries = 3
    retry_delay = 2
    
    for attempt in range(max_retries):
        try:
            print(f"[DEEPSEEK] Enviando requisição (tentativa {attempt + 1}/{max_retries})...")
            
            if stream:
                # Streaming response
                response = requests.post(url, json=payload, headers=headers, timeout=timeout, stream=True)
                response.raise_for_status()
                
                full_reply = ""
                for line in response.iter_lines():
                    if line:
                        line_str = line.decode('utf-8')
                        if line_str.startswith('data: '):
                            data_str = line_str[6:]  # Remove 'data: ' prefix
                            if data_str.strip() == '[DONE]':
                                break
                            try:
                                data = json.loads(data_str)
                                delta = data.get("choices", [{}])[0].get("delta", {})
                                content = delta.get("content", "")
                                if content:
                                    full_reply += content
                            except json.JSONDecodeError:
                                continue
                
                if full_reply:
                    print(f"[DEEPSEEK] Resposta recebida via stream ({len(full_reply)} caracteres)")
                    return full_reply.strip()
                else:
                    print(f"[DEEPSEEK] Resposta vazia do stream")
                    return ""
            else:
                # Non-streaming response
                response = requests.post(url, json=payload, headers=headers, timeout=timeout)
                response.raise_for_status()
                
                data = response.json()
                reply = data.get("choices", [{}])[0].get("message", {}).get("content", "")
                
                if reply:
                    print(f"[DEEPSEEK] Resposta recebida ({len(reply)} caracteres)")
                    return reply.strip()
                else:
                    print(f"[DEEPSEEK] Resposta vazia: {data}")
                    return ""
                
        except requests.exceptions.Timeout:
            print(f"[DEEPSEEK] Timeout na tentativa {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise
                
        except requests.exceptions.RequestException as e:
            print(f"[DEEPSEEK] Erro na requisição (tentativa {attempt + 1}): {e}")
            if hasattr(e, 'response') and e.response is not None:
                try:
                    error_detail = e.response.json()
                    print(f"[DEEPSEEK] Detalhes do erro: {error_detail}")
                except:
                    print(f"[DEEPSEEK] Status code: {e.response.status_code}")
                    print(f"[DEEPSEEK] Response text: {e.response.text[:500]}")
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise
        except Exception as e:
            print(f"[DEEPSEEK] Erro inesperado (tentativa {attempt + 1}): {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                retry_delay *= 2
            else:
                raise
    
    return ""

