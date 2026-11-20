import getpass
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from dotenv import dotenv_values
import mysql.connector
import psutil

ARQUIVO = Path(os.getenv("AEROGUARD_ENV", "/config/coletor.env"))
HOST_ROOT = Path(os.getenv("HOST_ROOT", "/"))


def normalizar_mac(valor):
    texto = valor.strip().replace(":", "").replace("-", "").lower()
    if not re.fullmatch(r"[0-9a-f]{12}", texto) or texto in ("000000000000", "ffffffffffff"):
        raise ValueError("MAC inválido.")
    return ":".join(texto[i:i + 2] for i in range(0, 12, 2))


def interfaces():
    resultado = {}
    for nome, enderecos in psutil.net_if_addrs().items():
        for endereco in enderecos:
            if endereco.family == psutil.AF_LINK:
                try:
                    resultado[nome] = normalizar_mac(endereco.address)
                except ValueError:
                    pass
    return resultado


def mac_principal():
    rotas = []
    for linha in (HOST_ROOT / "proc/net/route").read_text().splitlines()[1:]:
        campos = linha.split()
        if len(campos) >= 8 and campos[1] == "00000000" and int(campos[3], 16) & 1:
            rotas.append((int(campos[6]), campos[0]))
    locais = interfaces()
    for _, nome in sorted(rotas):
        if nome in locais:
            return locais[nome]
    raise ValueError("Não foi possível identificar o MAC da interface principal da EC2.")


def ler():
    dados = dict(dotenv_values(ARQUIVO, interpolate=False))
    for nome in ("DB_HOST", "DB_PORT", "DB_USER", "DB_PASSWORD", "EMPRESA_CNPJ", "MAC_ADDRESS"):
        if not dados.get(nome):
            raise ValueError(f"Configuração incompleta: {nome}.")
    if not re.fullmatch(r"[0-9]{14}", dados["EMPRESA_CNPJ"]):
        raise ValueError("CNPJ inválido na configuração salva.")
    dados["MAC_ADDRESS"] = normalizar_mac(dados["MAC_ADDRESS"])
    return dados


def conectar(dados):
    return mysql.connector.connect(
        host=dados["DB_HOST"], port=int(dados["DB_PORT"]), user=dados["DB_USER"],
        password=dados["DB_PASSWORD"], database="AeroGuard", autocommit=True,
        connection_timeout=3, read_timeout=5, write_timeout=5
    )


def conferir(dados):
    if dados["MAC_ADDRESS"] not in interfaces().values():
        raise ValueError("O MAC salvo não pertence a esta EC2. Não copie a configuração de outra máquina.")
    with conectar(dados) as conexao:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT razao_social FROM empresa WHERE cnpj = %s", (dados["EMPRESA_CNPJ"],))
            empresas = cursor.fetchall()
            if len(empresas) != 1:
                raise ValueError("A empresa não foi encontrada no banco.")
            for nome in ("CPU", "Memória", "Disco", "Rede"):
                cursor.execute("SELECT id FROM componentes WHERE nome = %s", (nome,))
                if len(cursor.fetchall()) != 1:
                    raise ValueError(f"O banco precisa de um único componente {nome}.")
            cursor.execute("SELECT DATA_TYPE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA='AeroGuard' "
                           "AND TABLE_NAME='configuracao_maquina' AND COLUMN_NAME='disponibilidade'")
            if cursor.fetchone() != ("tinyint",):
                raise ValueError("Atualize o banco para disponibilidade 0/1.")
    return empresas[0][0]


def ajustar_host_banco(entrada, host_padrao, porta_padrao):
    texto = entrada.strip()
    if not texto:
        return str(host_padrao), str(porta_padrao)
    texto = re.sub(r"^https?://", "", texto).split("/")[0]
    if ":" in texto:
        host, porta = texto.rsplit(":", 1)
        if not host:
            raise ValueError("IP do banco inválido.")
        if porta == "8080":
            return host, str(porta_padrao)
        if not porta.isdigit() or not 1 <= int(porta) <= 65535:
            raise ValueError("Porta do banco inválida.")
        return host, porta
    return texto, str(porta_padrao)


def configurar():
    if ARQUIVO.exists():
        dados = ler()
        empresa = conferir(dados)
        print(f"Configuração preservada: {empresa}, MAC {dados['MAC_ADDRESS']}.")
        return
    endereco = input("Endereço da central (ex.: 52.2.223.49:8080): ").strip().rstrip("/")
    if not endereco.startswith(("http://", "https://")):
        endereco = f"http://{endereco}"

    url = urlsplit(endereco)
    if url.scheme not in ("http", "https") or not url.hostname or url.username or url.password or url.path or url.query or url.fragment:
        raise ValueError("Informe o endereço da central com IP e porta válidos (ex.: 52.2.223.49:8080).")
    email = input("E-mail cadastrado no site: ").strip()
    senha = getpass.getpass("Senha do site: ")
    pedido = Request(endereco + "/usuarios/instalar",
                     data=json.dumps({"emailServer": email, "senhaServer": senha}).encode(),
                     headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urlopen(pedido, timeout=15) as resposta:
            resultado = json.load(resposta)
    except HTTPError as erro:
        if erro.code == 403:
            raise ValueError("E-mail ou senha inválidos.") from None
        raise ValueError(f"A central recusou a instalação (HTTP {erro.code}).") from None
    banco = resultado["banco"]
    entrada_banco = input(f"IP do banco de dados [Enter para usar {banco['host']}]: ")
    db_host, db_port = ajustar_host_banco(entrada_banco, banco["host"], banco["porta"])
    dados = {
        "DB_HOST": db_host, "DB_PORT": db_port,
        "DB_USER": str(banco["usuario"]), "DB_PASSWORD": str(banco["senha"]),
        "EMPRESA_CNPJ": str(resultado["empresa"]["cnpj"]), "MAC_ADDRESS": mac_principal()
    }
    if not re.fullmatch(r"[0-9]{14}", dados["EMPRESA_CNPJ"]) or not 1 <= int(dados["DB_PORT"]) <= 65535:
        raise ValueError("A central retornou uma configuração inválida.")
    if any(not valor or any(c in valor for c in "\n\r\0") for valor in dados.values()):
        raise ValueError("A central retornou uma configuração incompleta.")
    empresa = conferir(dados)
    ARQUIVO.parent.mkdir(parents=True, exist_ok=True)
    temporario = ARQUIVO.with_suffix(".tmp")
    temporario.write_text("".join(f"{nome}={valor}\n" for nome, valor in dados.items()), encoding="utf-8")
    temporario.chmod(0o600)
    temporario.replace(ARQUIVO)
    print(f"Empresa: {empresa}. MAC da EC2: {dados['MAC_ADDRESS']}.")


def maquina(cursor, dados):
    cursor.execute("""
        SELECT m.id FROM maquina m JOIN empresa e ON e.id=m.fkEmpresa
        WHERE e.cnpj=%s AND LOWER(REPLACE(REPLACE(TRIM(m.macAddress), ':', ''), '-', ''))=%s
    """, (dados["EMPRESA_CNPJ"], dados["MAC_ADDRESS"].replace(":", "")))
    linhas = cursor.fetchall()
    if len(linhas) > 1:
        raise ValueError("Existem máquinas duplicadas para a empresa e o MAC.")
    return linhas[0][0] if linhas else None


def ultima():
    dados = ler()
    with conectar(dados) as conexao:
        with conexao.cursor() as cursor:
            cursor.execute("SELECT COALESCE(MAX(id), 0) FROM captura WHERE maquina_id=%s", (maquina(cursor, dados),))
            print(cursor.fetchone()[0])


def capturas_novas(dados, limite):
    with conectar(dados) as conexao:
        with conexao.cursor() as cursor:
            identificador = maquina(cursor, dados)
            if identificador is None:
                return False
            for nome in ("CPU", "Memória", "Disco", "Rede"):
                cursor.execute("""
                    SELECT c.id, c.componentes_id, c.disponibilidade FROM configuracao_maquina c
                    JOIN componentes p ON p.id=c.componentes_id
                    WHERE c.maquina_captura_id=%s AND p.nome=%s
                    ORDER BY c.data_hora DESC, c.id DESC LIMIT 1
                """, (identificador, nome))
                configuracao = cursor.fetchone()
                if configuracao is None:
                    return False
                config_id, componente_id, ativo = configuracao
                if ativo == 0:
                    continue
                cursor.execute("""
                    SELECT DISTINCT tipo FROM captura WHERE id>%s AND maquina_id=%s AND componentes_id=%s
                    AND configuracao_maquina_maquina_id=%s AND configuracao_maquina_componentes_id=%s
                """, (limite, identificador, componente_id, config_id, config_id))
                tipos = {linha[0] for linha in cursor.fetchall()}
                if nome == "Rede":
                    if not any(t.startswith("Download - ") for t in tipos) or not any(t.startswith("Upload - ") for t in tipos):
                        return False
                elif f"Uso {nome}" not in tipos:
                    return False
    return True


def aguardar(limite):
    dados = ler()
    prazo = time.monotonic() + 75
    while time.monotonic() < prazo:
        if capturas_novas(dados, limite):
            print("Novas capturas confirmadas. Configurações inativas preservadas.")
            return
        time.sleep(2)
    raise ValueError("Faltam capturas de componentes ativos. Confira docker logs aeroguard-java e aeroguard-python.")


if __name__ == "__main__":
    try:
        if sys.argv[1:] == ["--configurar"]:
            configurar()
        elif sys.argv[1:] == ["--ultima"]:
            ultima()
        elif len(sys.argv) == 3 and sys.argv[1] == "--aguardar":
            aguardar(int(sys.argv[2]))
        else:
            raise ValueError("Use --configurar, --ultima ou --aguardar ID.")
    except (ValueError, KeyError, TypeError, OSError, URLError, mysql.connector.Error, psutil.Error, EOFError) as erro:
        print(f"Instalação interrompida: {erro}", file=sys.stderr)
        sys.exit(1)
