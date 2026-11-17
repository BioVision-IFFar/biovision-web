# Revisão do BioVision para a mostra

Alterações preparadas no código local. O servidor público não foi reiniciado
nem recebeu uploads de teste. Nenhuma biblioteca foi instalada/atualizada,
nenhum modelo ou credencial foi alterado e nenhuma regra de rede foi aplicada.

## Alterações e arquivos

| Arquivos | Resultado |
| --- | --- |
| `backend/biovision_web/db.py`, `web/static_biovision/species/homo_sapiens.jpg` | Ficha local de Homo sapiens com foto, classificação, habitat, morfologia, reprodução e características. Funciona para imagem e vídeo, sem alteração no MySQL. |
| `backend/biovision_web/model_service.py` | Air deixa de consultar e retornar taxonomia. Fila com reserva antes do recebimento, controle por visitante, resultados privados, expiração e prazo de processamento. Áudio passa por uma única decodificação limitada, sem executar FFmpeg externo. |
| `backend/biovision_web/media_security.py` | Nomes aleatórios no disco, gravação em blocos, formato real, resolução, pixels, duração, FPS, canais e taxa de áudio verificados. Somente arquivos locais privados e demuxers conhecidos. |
| `backend/biovision_web/security.py` | Lista explícita de recursos públicos, proteção contra leitura fora dessa pasta, limites de envios/progresso separados, cookie assinado, checagem de origem do formulário, cabeçalhos e erros sem detalhes internos. |
| `backend/biovision_web/routes.py`, `backend/biovision_web/__init__.py` | Todas as análises passam pela fila. Upload de um arquivo por pedido; caminhos, URLs e campos extras não são aceitos como fontes. Limites HTTP e limpeza após rejeição. |
| `app.py` | Limite de corpo também no Waitress, máximo de 100 conexões, canal inativo de 60 s, cabeçalhos de proxy não confiáveis removidos e rastreamentos desativados nas respostas. |
| `web/static_biovision/js/app.js` | Textos do banco escapados nos resultados de foto/vídeo/áudio. Air mostra somente a contagem e o animal escolhido, inclusive zero. Progresso tenta novamente se receber 429/503. |
| `web/templates/air.html`, `web/static_biovision/css/style.css` | Removida a faixa de identificação/contagem/análise ágil. Formulário aproximado do topo e título alterado para “Envie o vídeo e faça a contagem”. Aparência existente preservada. |
| `web/templates/identificar.html`, `web/templates/index.html`, `web/static_biovision/service-worker.js` | Versões de recursos/cache atualizadas e cache limitado aos recursos públicos conhecidos. |
| `.env.example`, `SECURITY.md`, `tests/` | Configurações documentadas, testes de regressão e prévia opcional separada. |

## Limites padrão

Valores de MB são multiplicados por 1.048.576 bytes. Há mais 64 KiB para o
envelope multipart; o arquivo continua sujeito ao limite individual abaixo.

| Item | Limite |
| --- | --- |
| Processamento | 2 análises executando; até 10 vagas restantes para recebimento/fila |
| Por visitante | Até 2 análises recebendo, aguardando ou executando |
| Novos envios | 6/min por visitante e 60/min no total |
| Consultas de progresso | 240/min por visitante e 3.600/min no total, separadas dos envios |
| Imagem | 10 MB; JPEG, PNG ou WEBP estático; lado até 8.192 px; até 24 milhões de pixels |
| Vídeo | 40 MB; MP4/MOV/M4V, AVI, MKV/WEBM; lado até 4.096 px; até 8.294.400 pixels por quadro; até 120 FPS |
| Duração de vídeo | Air: 30 s; identificação normal: 60 s, preservando os limites anteriores |
| Áudio | 15 MB; até 60 s; até 8 canais e 192 kHz na entrada; amostras decodificadas limitadas antes de acumular mais memória |
| Trabalho | Prazo cooperativo de 300 s, incluindo a espera por locks do modelo |
| Espera na fila | 600 s; se expirar, não inicia a análise quando chegar sua vez |
| Decodificação de áudio | 30 s, com checagens durante a leitura |
| Resultados | Expiração de 1 hora e retenção limitada a aproximadamente 200 registros |

Limites configuráveis em `.env.example`. **Se o `.env` do servidor ainda tiver
`BIOVISION_ANALYSIS_WORKERS=4`, ajuste somente essa configuração para `2`** para
adotar o padrão da mostra. Não substitua o `.env` nem suas credenciais pelo
arquivo de exemplo. A configuração antiga `BIOVISION_MAX_ACTIVE_JOBS` foi
substituída por `BIOVISION_MAX_QUEUED_JOBS`.

429 indica excesso do visitante; 503 indica capacidade ou limite global
atingido. Essas respostas incluem `Retry-After`. Arquivos grandes recebem 413.
Uploads aceitos recebem 202 e devem ser acompanhados pelo `status_url`;
`async=0` não contorna a fila. A validação detalhada acontece na fila e, se
reprovar a mídia, o job termina com uma mensagem clara sem executar o modelo.

## Rede e arquivos

O visitante recebe um cookie aleatório assinado, HttpOnly e SameSite=Strict.
Vários visitantes podem compartilhar o IP da ponte sem dividir um pequeno
limite individual por IP. `X-Forwarded-For`, `CF-Connecting-IP` e
`X-Real-IP` não são usados como identidade nem para conceder acesso.
Cada resultado só pode ser consultado com o cookie que criou a análise.

`BIOVISION_TRUSTED_PROXY_IPS` é opcional: lista IPs exatos da ponte que podem
informar HTTPS pelo `X-Forwarded-Proto` para a flag Secure do cookie, se esse
cabeçalho chegar à aplicação. O Waitress remove cabeçalhos não confiáveis.
Não há confiança automática nos cabeçalhos, nem aplicação automática de regra
de firewall. Caso se decida restringir a porta 5001 à ponte, é necessário
primeiro obter seu IP e revisar a regra com o responsável.

Só os arquivos explicitamente listados em `PUBLIC_STATIC_FILES` são servidos.
Arquivos temporários usam diretórios `biovision_upload_*` no diretório temporário
do sistema, sem rota pública; nomes/extensões enviados pelo visitante não são
usados no disco. Limpeza ocorre após sucesso, falha e rejeição. Uploads não são
passados a shell, `eval`, `exec` nem carregadores de modelos.

PyAV recebe o formato detectado e somente o protocolo `file`; playlists e
demuxers de scripts ficam fora da lista. Referências externas de MOV ficam
desativadas. As mesmas opções são configuradas para o leitor FFmpeg do OpenCV,
usado pelo Ultralytics. Isso reduz os caminhos de acesso a recursos externos,
mas não constitui isolamento do processo.

## Verificação sem afetar o serviço

Com o Python 3.11 e as dependências já instaladas neste PC:

```powershell
py -3.11 -m unittest discover -s tests -v
node tests/frontend-security.cjs
node --check web/static_biovision/js/app.js
node --check web/static_biovision/service-worker.js
```

Os testes usam o cliente de teste do Flask, sem abrir porta, sem MySQL e sem
carregar modelos. Criam PNG pequeno, MP4 de três quadros e WAV de um segundo.
A inferência é simulada; Pillow, PyAV, OpenCV, roteamento, cookies, limites,
fila, isolamento de resultados e limpeza são exercitados de verdade.
Os testes de capacidade reservam vagas em memória; não são testes de carga.
As verificações de HTML usam dados sintéticos no Node, sem enviá-los ao site.

Resultado após recuperar as fichas e imagens: **34 testes Python aprovados**, verificação de HTML no Node
aprovada e sintaxe de Python/JavaScript validada. A inferência dos modelos foi
simulada; a precisão e o tempo dos modelos no PC servidor não foram medidos.

A prévia opcional `py -3.11 tests/preview_local.py` usa apenas `127.0.0.1` e
uma porta livre; não ocupa 5001 e não carrega modelos. Ela tem duas páginas de
amostra: `/preview/human` e `/preview/air`. Essas rotas só existem nesse script,
nunca no servidor normal. A foto e as abas de taxonomia, o novo cabeçalho do Air
e seu resultado simplificado foram conferidos no navegador local.

### Conferência de celular

A prévia também foi conferida em viewports de 320 × 568, 360 × 800,
390 × 844 e 430 × 932 pixels, além de 812 × 375 em orientação horizontal.
Foram verificadas a página inicial, a ficha humana e a contagem do Air.

- Corrigida uma regra antiga que colocava a quantidade antes do nome do animal
  no celular. Agora a ordem é nome e quantidade, com alinhamento consistente.
- Cabeçalho do resultado quebra de linha quando necessário; uma contagem de
  teste de 99.999 também coube na tela de 320 pixels.
- Reduzido o espaço vazio antes do formulário do Air. Título, logo e fundo se
  ajustam à largura disponível, preservando a identidade visual.
- Opções dos seletores têm pelo menos 44 pixels de altura para facilitar o
  toque. Os modos de imagem/áudio e a seleção do animal foram conferidos.
- Foto, taxonomia, navegação e títulos permaneceram dentro da largura da tela.
- CSS e cache do service worker foram versionados novamente para distribuir
  esses ajustes. O pacote de atualização inclui a versão para celular.

Essas verificações usaram o navegador local com tamanhos de tela definidos;
não envolveram aparelhos físicos nem o servidor público.

### Recuperação das fichas e imagens do R2

A base de imagens foi atualizada para o R2 público informado, preservando os
caminhos `especies886/` e as extensões originais. Foi incluído um catálogo
privado de recuperação com as 876 fichas do dump, cobrindo todas as classes do
modelo, para uso quando o MySQL não estiver configurado, falhar ou faltar um
campo. Dados preenchidos no MySQL continuam tendo prioridade.

As prévias de foto, vídeo e áudio carregaram imagens reais `.jfif`, `.jpg` e
`.png` do R2. As fichas foram conferidas em 320, 390 e 430 pixels, além de
812 × 375 em orientação horizontal. A coluna da taxonomia em cartões menores
de vídeo/áudio foi corrigida para caber sem recortar os valores.

Arquivos adicionais: `backend/data/species_catalog.json.gz`,
`scripts/export_species_catalog.py`, `tests/test_species_data.py` e
`docs/IMAGENS-E-FICHAS.md`. Configuração, consulta de fichas, documentação e
versões do CSS/service worker também foram atualizadas. Nenhum SQL foi
executado nem modelo, credencial ou serviço MySQL foi alterado.

Detalhes e instruções: [Recuperação das imagens e fichas](IMAGENS-E-FICHAS.md).

## Ativação e preservação do endereço

1. Levar os arquivos alterados para o checkout que executa o Python no PC
   servidor. Incluir os dois módulos novos e a foto. Não copiar credenciais,
   modelos nem o `.git` de outro computador.
2. Conferir apenas os parâmetros de limites no `.env`, especialmente os workers.
3. Quando for conveniente, aguardar os trabalhos atuais e reiniciar **somente o
   processo Python** usando o comando habitual. Nenhum reinício foi feito aqui.
4. Manter o processo `cloudflared` da ponte rodando e sua configuração atual.
   A aplicação continua na porta 5001 e nas rotas `/biovision/` já usadas.
5. Recarregar a página para receber o JavaScript/CSS/cache novos. Jobs antigos
   são perdidos ao reiniciar o Python; cookies de análise são renovados.

Backend, fila, limites, validação, cabeçalhos e ficha local entram em vigor no
novo processo Python. Interface e cache entram quando o navegador busca as
novas versões. Não é necessário reiniciar o túnel para ativar essas alterações.

Pacote de atualização preparado em `tmp/biovision-mostra-atualizacao.zip`, com
os arquivos alterados/novos e instruções de cópia. Não inclui `.env`, modelos,
banco, logs nem metadados Git.

O endereço `trycloudflare.com` é temporário e gerado pelo Quick Tunnel: pode
mudar se o processo do túnel for encerrado/recriado. Preservar rotas e porta
evita uma mudança de endereço causada pela aplicação, mas não garante a
permanência do serviço externo. [Documentação dos Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/).

## Limitações restantes

- O prazo de 300 s é cooperativo: checado entre etapas, quadros, lotes e ao
  aguardar locks. Uma chamada nativa de PyTorch, OpenCV ou PyAV que trave não
  pode ser interrompida com segurança por uma thread Python. A vaga só é liberada
  quando a execução realmente sai; timeout do navegador não cancela o trabalho.
- Não há sandbox do processo nem bloqueio de saída no sistema operacional.
  Restrições de protocolo/formato não eliminam vulnerabilidades dos codecs,
  bibliotecas, modelos confiáveis ou Windows, que não foram atualizados.
- Cookies podem ser descartados/renovados pelo visitante para contornar limites
  individuais. Os limites globais e a fila continuam limitando a aplicação.
- O Waitress recebe o corpo HTTP antes de entregá-lo ao Flask. A reserva da fila
  precede a leitura/gravação pela aplicação, mas não impede todo consumo de rede
  ou disco durante a recepção pelo servidor HTTP. O limite de corpo e o teto de
  100 conexões reduzem esse consumo; não são proteção completa contra sobrecarga.
- Fila e limites residem na memória de **um processo**. A configuração Gunicorn
  existente usa um worker; vários processos/servidores precisariam de limites
  compartilhados. Reinícios perdem os jobs pendentes e seus resultados.
- Queda abrupta do processo/Windows pode deixar temporários no disco; o fluxo
  normal faz a limpeza. Não foi feita remoção de arquivos antigos de outros
  processos, pois isso poderia afetar o serviço atual.
- Sem antivírus adicional, testes destrutivos, testes de sobrecarga, análise
  completa de dependências ou validação da infraestrutura do amigo. A precisão
  dos modelos não foi reavaliada. O link público não pôde ser inspecionado pela
  ferramenta de consulta, e não recebeu testes ativos.
- A porta 5001 mantém a exposição de rede anterior; não foi limitada à ponte.
  A política de scripts preserva o JavaScript inline existente para manter a
  interface; não é uma política CSP estrita sem inline.

## Fontes da ficha humana

- Taxonomia: [ITIS, Homo sapiens, TSN 180092](https://www.itis.gov/servlet/SingleRpt/SingleRpt?search_topic=TSN&search_value=180092).
- Foto: retrato público de Christina Koch, NASA, 2023, [página de origem](https://www.nasa.gov/feature/our-artemis-crew/), [imagem](https://www.nasa.gov/wp-content/uploads/2023/06/jsc2023e0016435-alt.jpg?w=768).
- Uso factual/educativo, sem associação de apoio da NASA ao projeto, conforme
  [diretrizes de uso de mídia](https://www.nasa.gov/nasa-brand-center/images-and-media/).
