import io
import re
import time

import pandas as pd
import requests
import streamlit as st

# Tenta importar DNS (para validar domínio de e-mail)
try:
    import dns.resolver as dns_resolver
except ImportError:
    dns_resolver = None


# ==========================================================
# Funções de negócio (validação e enriquecimento)
# ==========================================================

def email_valido_formato(email: str) -> bool:
    """Valida o FORMATO básico do e-mail (não garante que ele exista)."""
    if not isinstance(email, str):
        return False

    email = email.strip()

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


def extrair_dominio(email: str):
    """Extrai o domínio (parte depois do @) ou None se não der."""
    if not isinstance(email, str):
        return None
    email = email.strip()
    if "@" not in email:
        return None
    dom = email.split("@")[-1].strip().lower()
    return dom or None


def dominio_existe(dominio: str) -> bool:
    """
    Tenta descobrir se o domínio existe via DNS.
    Primeiro tenta MX (e-mail), depois A (site).
    Se não tiver dnspython ou der erro, devolve False.
    """
    if not isinstance(dominio, str):
        return False

    dominio = dominio.strip().lower()
    if not dominio:
        return False

    if dns_resolver is None:
        # Sem dnspython não dá pra checar de verdade
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


def limpar_cnpj(cnpj: str):
    """Remove tudo que não é dígito e garante 14 dígitos."""
    if not isinstance(cnpj, str):
        cnpj = str(cnpj)
    digitos = re.sub(r"\D", "", cnpj)
    if len(digitos) != 14:
        return None
    return digitos


def consultar_cnpj_api(cnpj_limpo: str):
    """
    Consulta CNPJ na API pública CNPJ.ws.
    Atenção: limite de ~3 requisições por minuto no gratuito.
    """
    base_url = "https://publica.cnpj.ws/cnpj/"

    try:
        resp = requests.get(base_url + cnpj_limpo, timeout=10)

        if resp.status_code == 429:
            # limite estourado
            return None

        if resp.status_code != 200:
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

    except Exception:
        return None


def segmento_macro_por_cnae(cnae_codigo: str) -> str:
    """Agrupa o CNAE em um segmento macro simplificado."""
    if not isinstance(cnae_codigo, str):
        cnae_codigo = str(cnae_codigo or "")

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
        return "Água, esgoto, resíduos"
    if 41 <= sec <= 43:
        return "Construção"
    if 45 <= sec <= 47:
        return "Comércio / Varejo"
    if 49 <= sec <= 53:
        return "Transporte e correio"
    if 55 <= sec <= 56:
        return "Alojamento e alimentação"
    if 58 <= sec <= 63:
        return "Informação e comunicação"
    if 64 <= sec <= 66:
        return "Finanças e seguros"
    if sec == 68:
        return "Imobiliário"
    if 69 <= sec <= 75:
        return "Serviços profissionais"
    if 77 <= sec <= 82:
        return "Serviços administrativos"
    if sec == 84:
        return "Administração pública"
    if sec == 85:
        return "Educação"
    if 86 <= sec <= 88:
        return "Saúde e assistência social"
    if 90 <= sec <= 93:
        return "Artes, esporte e recreação"
    if 94 <= sec <= 96:
        return "Outros serviços"
    if 97 <= sec <= 98:
        return "Serviços domésticos"
    if sec == 99:
        return "Organismos internacionais"

    return ""


def enriquecer_dataframe(df: pd.DataFrame, col_email: str, col_cnpj: str) -> pd.DataFrame:
    """Aplica todas as validações/enriquecimentos na base."""

    st.write("▶️ Iniciando enriquecimento da base...")

    # 1) Validação de formato de e-mail
    st.write("📧 Validando formato dos e-mails...")
    df["email_valido_formato"] = df[col_email].apply(email_valido_formato)

    # 2) Validação de domínio de e-mail
    st.write("🌐 Checando se domínio dos e-mails existe...")
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

    df["dominio_existe"] = df[col_email].apply(checar_dominio_email)

    # 3) Consulta de CNPJ com gotejamento + cache em memória
    st.write("🏢 Consultando CNPJ na API pública (gotejamento, pode demorar)...")

    cnpj_cache = {}
    chamadas_api_no_ciclo = 0
    max_chamadas_por_ciclo = 3
    tempo_espera = 65  # segundos entre blocos

    resultados = []

    for idx, cnpj in enumerate(df[col_cnpj].tolist()):
        cnpj_limpo = limpar_cnpj(cnpj)
        if not cnpj_limpo:
            resultados.append(
                {
                    "cnpj_situacao_cadastral": None,
                    "cnae_principal_codigo": None,
                    "cnae_principal_descricao": None,
                    "segmento_macro": None,
                }
            )
            continue

        if cnpj_limpo not in cnpj_cache:
            if chamadas_api_no_ciclo >= max_chamadas_por_ciclo:
                st.write("⏳ Limite de consultas atingido. Aguardando alguns segundos...")
                time.sleep(tempo_espera)
                chamadas_api_no_ciclo = 0

            info = consultar_cnpj_api(cnpj_limpo)
            cnpj_cache[cnpj_limpo] = info
            chamadas_api_no_ciclo += 1
        else:
            info = cnpj_cache[cnpj_limpo]

        if not info:
            resultados.append(
                {
                    "cnpj_situacao_cadastral": None,
                    "cnae_principal_codigo": None,
                    "cnae_principal_descricao": None,
                    "segmento_macro": None,
                }
            )
        else:
            seg = segmento_macro_por_cnae(info.get("cnae_principal_codigo") or "")
            resultados.append(
                {
                    "cnpj_situacao_cadastral": info.get("situacao_cadastral"),
                    "cnae_principal_codigo": info.get("cnae_principal_codigo"),
                    "cnae_principal_descricao": info.get("cnae_principal_descricao"),
                    "segmento_macro": seg,
                }
            )

    enriquecido_df = pd.DataFrame(resultados)
    df_final = pd.concat([df.reset_index(drop=True), enriquecido_df], axis=1)

    return df_final


# ==========================================================
# App Streamlit (frontend)
# ==========================================================

def main():
    st.set_page_config(
        page_title="Valida Prospect – Enriquecimento de Base",
        layout="wide",
    )

    st.title("🧠 Valida Prospect – Validador & Enriquecedor de Base de Clientes")
    st.write(
        """
        Envie um arquivo com colunas de **empresa, CNPJ e e-mail** e receba de volta uma base
        enriquecida com:
        - ✅ Validação de formato de e-mail  
        - ✅ Checagem de domínio (DNS)  
        - ✅ Consulta de CNPJ em API pública  
        - ✅ Segmento macro por CNAE  
        """
    )

    uploaded_file = st.file_uploader(
        "Envie seu arquivo (CSV com ; ou Excel)",
        type=["csv", "xlsx"],
    )

    if not uploaded_file:
        st.info("👆 Envie um arquivo para começar.")
        return

    # Detectar tipo do arquivo
    if uploaded_file.name.lower().endswith(".csv"):
        # Supondo separador ; como padrão Brasil
        df = pd.read_csv(uploaded_file, sep=";")
    else:
        df = pd.read_excel(uploaded_file)

    st.subheader("Pré-visualização da base enviada")
    st.dataframe(df.head())

    # Seleção das colunas de e-mail e CNPJ (flexível)
    st.subheader("Mapeamento de colunas")

    colunas = list(df.columns)

    col_email = st.selectbox(
        "Coluna de e-mail",
        colunas,
        index=colunas.index("Email") if "Email" in colunas else 0,
    )
    col_cnpj = st.selectbox(
        "Coluna de CNPJ",
        colunas,
        index=colunas.index("CNPJ") if "CNPJ" in colunas else 0,
    )

    if st.button("🚀 Processar base"):
        df_enriquecido = enriquecer_dataframe(df, col_email, col_cnpj)

        st.subheader("Base enriquecida (primeiras linhas)")
        st.dataframe(df_enriquecido.head())

        # Visualizações simples
        st.subheader("📊 Visualizações rápidas")

        if "segmento_macro" in df_enriquecido.columns:
            seg_counts = df_enriquecido["segmento_macro"].value_counts(dropna=True)
            if not seg_counts.empty:
                st.write("Distribuição por segmento macro:")
                st.bar_chart(seg_counts)

        if "cnpj_situacao_cadastral" in df_enriquecido.columns:
            sit_counts = df_enriquecido["cnpj_situacao_cadastral"].value_counts(
                dropna=True
            )
            if not sit_counts.empty:
                st.write("Situação cadastral dos CNPJs:")
                st.bar_chart(sit_counts)

        # Gerar arquivo Excel para download
        buffer = io.BytesIO()
        df_enriquecido.to_excel(buffer, index=False)
        buffer.seek(0)

        st.download_button(
            label="⬇️ Baixar base enriquecida (Excel)",
            data=buffer,
            file_name="base_enriquecida.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


if __name__ == "__main__":
    main()
