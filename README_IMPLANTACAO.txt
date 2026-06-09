DASHBOARD ONLINE DE LEADS - CONSTRUTEC
======================================

Objetivo
--------
Transformar o CSV em uma base viva, conectada ao HTML online.
O painel lê o arquivo leads.csv pelo servidor e atualiza automaticamente a cada 10 segundos.
Quando um lead é editado, criado ou excluído no painel, o servidor grava novamente no CSV.

Arquivos
--------
- dashboard_online.html: painel online conectado à API.
- server.py: servidor Python que lê/grava leads.csv.
- leads.csv: base principal de leads.
- backups/: criada automaticamente; guarda cópias do CSV antes de cada alteração.

Como rodar no computador
------------------------
1. Coloque todos os arquivos na mesma pasta.
2. Abra o terminal dentro da pasta.
3. Execute:

   python server.py

4. Abra no navegador:

   http://localhost:8000

Como atualizar em tempo real
----------------------------
- Edite o arquivo leads.csv diretamente, ou por Excel/LibreOffice.
- Salve o CSV.
- O painel puxa as mudanças automaticamente a cada 10 segundos.
- Também é possível clicar em "Atualizar agora".

Como alimentar pelo próprio painel
----------------------------------
- Clique em "Novo lead" para criar um registro.
- Edite os campos da tabela.
- Clique em "Salvar" na linha.
- O servidor grava no leads.csv.

Publicar online
---------------
Para publicar de verdade, use um serviço que rode Python, como Render, Railway, VPS, Hostinger VPS ou servidor próprio.
Não use apenas GitHub Pages ou hospedagem HTML estática se quiser salvar alterações no CSV, porque site estático não grava arquivo.

Comando de start para Render/Railway:

   python server.py

Porta
-----
O servidor usa a variável PORT quando existir. Caso contrário, usa 8000.

Segurança opcional
------------------
Para proteger gravações online, defina uma variável de ambiente:

   API_TOKEN=sua_senha_aqui

Depois, no painel, clique em "Definir token" e informe o mesmo token.
Sem token, qualquer pessoa com acesso ao painel pode editar o CSV.

Formato recomendado do CSV
--------------------------
id,lead,nome,fonte,status,valor,data,link,email,perfil,numero

Status aceitos no dashboard:
- Captado
- Visualizado
- Tratado
- Retorno
- Fechado-Venda
- Fechado-Perda

Observação
----------
Esta solução usa atualização por consulta automática/polling a cada 10 segundos.
Para volume comercial da Construtec, isso já resolve bem e é mais simples que WebSocket.
