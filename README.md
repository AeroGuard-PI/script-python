# Cliente Python

Coleta CPU, memória e ocupação do sistema de arquivos principal. O Java coleta rede.

O instalador executa configurar.py uma vez para fazer login na central, identificar a empresa e obter o MAC da EC2. As informações são salvas automaticamente em `/opt/aeroguard/config/coletor.env`. A senha pessoal do login não fica nesse arquivo.

O Dockerfile inicia o programa com `--coletar`, sem menu. O programa mantém consultas e transações próprias em cada ciclo e volta a tentar depois de falhas.

Para usar os menus em um terminal Ubuntu com acesso à central:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
AEROGUARD_ENV=.env python configurar.py --configurar
python script-coleta-python.py
```

configurar.py pede o endereço da central e o login do site e gera o .env local. Não é preciso preencher o CNPJ ou o MAC manualmente. .env.example mostra os campos usados; o banco é AeroGuard.

As telas de uso mostram o valor, o status e o horário da última captura gravada pela coleta. Abrir um menu não grava outra captura nem interfere nos 30 segundos de observação. As opções de componentes acompanham as configurações ativas; a consulta ao banco mostra as últimas capturas da empresa vinculada ao login.

CPU, Memória e Disco são consultados separadamente. Cada componente recebe uma configuração somente quando ainda não existe. Disponibilidade 0 é preservada. Os IDs de ALERTA e CRÍTICO e seus limites são consultados pelo nome; o seed define 80% e 90%. A faixa crítica precisa permanecer nas leituras por 30 segundos.

No Docker, o Python lê CPU e memória do `/proc` da EC2 e a ocupação de `/host`, que corresponde ao `/` do host. Disco não significa taxa de leitura/escrita nem todos os discos físicos. Os horários persistidos usam UTC; os menus apresentam esses horários no fuso do ambiente em execução.

As bibliotecas ficam em requirements.txt. A instalação dos dois coletores é feita pelo script-instalacao-cliente.sh do repositório [script-instalacao-pi](https://github.com/AeroGuard-PI/script-instalacao-pi). Consulte o README daquele repositório para instalar e acompanhar os containers.
