import re
import time
import json
import os
import pandas as pd

# Tenta importar a biblioteca de DNS (para checar domínio de e-mail)
try:
    import dns.resolver as dns_resolver
except ImportError:
    dns_resolver = None

import requests


# ==========================================================
# 1. Validação de FORMATO do e-mail
# ==========================================================
def email_valido_formato(email: str) -> bool:
    if not isinstance(email, str):
        return False

    email = str(email).strip()

    if "@" not in email:
        return False

    partes = email.split("@")
    if len(partes) != 2:
        return False

    usuario, dominio = partes

    if not usuario.strip():
        return False

    if not dominio.strip():
        return False

    if "." not in dominio:
        return False

    if dominio.endswith("."):
        return False

    return True


# ==========================================================
# 2. Checar se o DOMÍNIO do e-mail existe (DNS)
# ==========================================================
def extrair_dominio(email: str):
    if not isinstance(email, str):
        return None
    email = email.strip()
    if "@" not in email:
        return None
    dom = email.split("@")[-1].strip().lower()
    return dom or None


def dominio_existe(dominio: str) -> bool:
    """
    Usa DNS para tentar descobrir se o domínio existe.
    Primeiro tenta registro MX (e-mail), depois A (site).
    Se der erro, devolve False.
    """
    if not isinstance(dominio, str):
        return False

    dominio = dominio.strip().lower()
    if not dominio:
        return False

    if dns_resolver is None:
        # Se a lib dnspython não estiver disponível, não conseguimos checar de fato
        return False

    try:
        dns_resolver.resolve(dominio, "MX")
        return True
    except Exception:
        try:
            dns_resolver.resolve(dominio, "A")
            return True
        except Exception:
            return False


# ==========================================================
# 3. Funções para CNPJ: limpar, consultar API, extrair CNAE/segmento
# ==========================================================
def limpar_cnpj(cnpj: str):
    if not isinstance(cnpj, str):
        return None
    digitos = re.sub(r"\D", "", cnpj)
    if len(digitos) != 14:
        return None
    return digitos


def consultar_cnpj_api(cnpj_limpo: str):
    """
    Usa a API pública do CNPJ.ws:
    https://publica.cnpj.ws/cnpj/{cnpj}
    Limite: 3 requisições por minuto no plano gratuito.
    """
    base_url = "https://publica.cnpj.ws/cnpj/"

    try:
        resp = requests.get(base_url + cnpj_limpo, timeout=10)

        # Se passou do limite
        if resp.status_code == 429:
            print(f"⚠️  Recebido 429 (muitas requisições) para CNPJ {cnpj_limpo}.")
            return None

        if resp.status_code != 200:
            print(f"⚠️  Erro HTTP {resp.status_code} para CNPJ {cnpj_limpo}.")
            return None

        data = resp.json()
        if not isinstance(data, dict):
            return None

        estabelecimento = data.get("estabelecimento", {}) or {}
        if not isinstance(estabelecimento, dict):
            estabelecimento = {}

        situacao = (
            estabelecimento.get("situacao_cadastral")
            or data.get("situacao_cadastral")
        )

        cnae_principal = estabelecimento.get("atividade_principal") or {}
        if isinstance(cnae_principal, dict):
            cnae_codigo = (
                cnae_principal.get("id")
                or cnae_principal.get("codigo")
                or ""
            )
            cnae_desc = cnae_principal.get("descricao") or ""
        else:
            cnae_codigo = ""
            cnae_desc = ""

        return {
            "situacao_cadastral": situacao,
            "cnae_principal_codigo": cnae_codigo,
            "cnae_principal_descricao": cnae_desc,
        }

    except Exception as e:
        print(f"⚠️  Erro ao consultar CNPJ {cnpj_limpo}: {e}")
        return None


def segmento_macro_por_cnae(cnae_codigo: str) -> str:
    """
    Agrupa por segmento macro usando os 2 primeiros dígitos do CNAE.
    É uma simplificação, mas já ajuda bastante em análise.
    """
    if not isinstance(cnae_codigo, str):
        return ""

    digitos = re.sub(r"\D", "", cnae_codigo)
    if len(digitos) < 2:
        return ""

    sec = int(digitos[:2])

    if 1 <= sec <= 3:
        return "Agropecuária"
    if 5 <= sec <= 9:
        return "Indústrias extrativas"
    if 10 <= sec <= 33:
        return "Indústrias de transformação"
    if sec == 35:
        return "Eletricidade e gás"
    if 36 <= sec <= 39:
        return "Água, esgoto, gestão de resíduos"
    if 41 <= sec <= 43:
        return "Construção"
    if 45 <= sec <= 47:
        return "Comércio; reparo de veículos"
    if 49 <= sec <= 53:
        return "Transporte, armazenagem e correio"
    if 55 <= sec <= 56:
        return "Alojamento e alimentação"
    if 58 <= sec <= 63:
        return "Informação e comunicação"
    if 64 <= sec <= 66:
        return "Atividades financeiras e de seguros"
    if sec == 68:
        return "Atividades imobiliárias"
    if 69 <= sec <= 75:
        return "Serviços profissionais, científicos e técnicos"
    if 77 <= sec <= 82:
        return "Serviços administrativos e complementares"
    if sec == 84:
        return "Administração pública"
    if sec == 85:
        return "Educação"
    if 86 <= sec <= 88:
        return "Saúde humana e serviços sociais"
    if 90 <= sec <= 93:
        return "Artes, cultura, esporte e recreação"
    if 94 <= sec <= 96:
        return "Outras atividades de serviços"
    if 97 <= sec <= 98:
        return "Serviços domésticos"
    if sec == 99:
        return "Organismos internacionais"

    return ""


# ==========================================================
# 4. PROGRAMA PRINCIPAL
# ==========================================================
def main():
    # Ajustado para sua planilha:
    arquivo_entrada = "clientes.csv"
    coluna_email = "Email"   # exatamente como está na planilha
    coluna_cnpj = "CNPJ"     # exatamente como está na planilha

    print("Lendo planilha de clientes...")

    # CSV brasileiro normalmente vem com ; como separador
    df = pd.read_csv(arquivo_entrada, sep=";")

    # 4.1 Validar formato de e-mail
    print("Validando FORMATO dos e-mails...")
    df["email_valido_formato"] = df[coluna_email].apply(email_valido_formato)

    # 4.2 Validar se o domínio existe (DNS)
    print("Checando se domínio de e-mail existe (DNS)...")
    dominio_cache = {}

    def checar_dominio_email(email):
        dom = extrair_dominio(email)
        if not dom:
            return False
        if dom in dominio_cache:
            return dominio_cache[dom]
        ok = dominio_existe(dom)
        dominio_cache[dom] = ok
        return ok

    df["dominio_existe"] = df[coluna_email].apply(checar_dominio_email)

    # 4.3 Consultar CNPJ com gotejamento (rate limit) + cache em arquivo
    print("Consultando CNPJ na API pública em gotejamento (3 por minuto)...")

    CACHE_FILE = "cnpj_cache.json"

    # carrega cache de disco, se existir
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cnpj_cache = json.load(f)
    else:
        cnpj_cache = {}

    chamadas_api_no_ciclo = 0
    max_chamadas_por_ciclo = 3
    tempo_espera = 65  # segundos entre blocos de 3 chamadas novas

    def enriquecer_cnpj(cnpj):
        nonlocal chamadas_api_no_ciclo, cnpj_cache

        cnpj_limpo = limpar_cnpj(str(cnpj))
        if not cnpj_limpo:
            return pd.Series(
                {
                    "cnpj_situacao_cadastral": None,
                    "cnae_principal_codigo": None,
                    "cnae_principal_descricao": None,
                    "segmento_macro": None,
                }
            )

        # Se já temos no cache (de execuções anteriores ou desta), não consulta de novo
        if cnpj_limpo not in cnpj_cache:
            # Se já batemos o limite, espera e reseta o contador
            if chamadas_api_no_ciclo >= max_chamadas_por_ciclo:
                print("⏳ Limite de chamadas no ciclo atingido. Aguardando 65 segundos...")
                time.sleep(tempo_espera)
                chamadas_api_no_ciclo = 0

            info = consultar_cnpj_api(cnpj_limpo)
            cnpj_cache[cnpj_limpo] = info  # pode ser dict ou None
            chamadas_api_no_ciclo += 1
        else:
            info = cnpj_cache[cnpj_limpo]

        if not info:
            return pd.Series(
                {
                    "cnpj_situacao_cadastral": None,
                    "cnae_principal_codigo": None,
                    "cnae_principal_descricao": None,
                    "segmento_macro": None,
                }
            )

        seg = segmento_macro_por_cnae(info.get("cnae_principal_codigo") or "")

        return pd.Series(
            {
                "cnpj_situacao_cadastral": info.get("situacao_cadastral"),
                "cnae_principal_codigo": info.get("cnae_principal_codigo"),
                "cnae_principal_descricao": info.get("cnae_principal_descricao"),
                "segmento_macro": seg,
            }
        )

    enriquecido = df[coluna_cnpj].apply(enriquecer_cnpj)
    df = pd.concat([df, enriquecido], axis=1)

    # salva cache atualizado em disco
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cnpj_cache, f, ensure_ascii=False)

    # 4.4 Gerar nome de arquivo único com timestamp
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    saida = f"clientes_validado_completo_{timestamp}.xlsx"

    df.to_excel(saida, index=False)

    print(f"\n✅ Processo concluído! Arquivo gerado com sucesso: {saida}")
    print("   (Os CNPJs já consultados ficaram salvos em cnpj_cache.json.)")


if __name__ == "__main__":
    main()
