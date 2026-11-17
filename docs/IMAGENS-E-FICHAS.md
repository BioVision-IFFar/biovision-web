# Recuperação das imagens e fichas

## O que foi encontrado

- As fichas são consultadas por `backend/biovision_web/db.py`, na tabela MySQL
  `especie`, usando o nome científico identificado pelo modelo.
- O banco armazena os caminhos reais dos arquivos. Exemplo:
  `/especies886/Especie/Vanellus_chilensis.jfif`.
- O exemplo de configuração ainda apontava para
  `biovision-images.pedrohenriquetoazza.workers.dev` e o código não tinha uma base
  padrão quando `BIOVISION_IMAGE_BASE_URL` estava ausente.
- O serviço MySQL80 foi encontrado em execução neste PC, mas este checkout não
  tem `.env` com uma conexão configurada. Isso é independente do endereço R2 e
  também explica a ausência das informações neste ambiente.
- O dump do próprio projeto contém **876 fichas**, cobrindo todas as **876
  classes** do modelo. Não foi necessário inventar dados nem consultar fontes
  externas para reconstruir esse acervo.

## Como ficou

O MySQL configurado continua tendo prioridade. Se a conexão não estiver
configurada, falhar ou faltar uma ficha/campo, o backend usa a cópia local do
mesmo acervo, em `backend/data/species_catalog.json.gz`. Campos preenchidos no
MySQL não são substituídos. O catálogo não é servido por uma rota pública.

O exportador `scripts/export_species_catalog.py` produziu essa cópia lendo o
dump como dados: **nenhum comando SQL foi executado**, nenhuma tabela foi
recriada e nenhuma credencial foi modificada. O SQL continua disponível para
quem já utiliza MySQL; o catálogo é somente uma recuperação de leitura.

Base atual das imagens:

```dotenv
BIOVISION_IMAGE_BASE_URL=https://pub-aad8385f455d485ba068da0a6bc8efe3.r2.dev
```

Sem essa variável, o código já usa a base acima. Se o `.env` ou algum registro
ainda apontar para o Worker antigo conhecido, o código migra essa origem ao
montar a URL. Outros endereços explicitamente configurados continuam respeitados.

O caminho não é reconstruído a partir do nome do animal e sua extensão não é
trocada: uma foto `.jfif` continua `.jfif`, uma morfologia `.jpg` continua
`.jpg`, e figuras `.png` continuam `.png`. Barras e codificação de espaços e
acentos são normalizadas, preservando maiúsculas/minúsculas das pastas.

Exemplo completo:

[Foto do quero-quero no R2](https://pub-aad8385f455d485ba068da0a6bc8efe3.r2.dev/especies886/Especie/Vanellus_chilensis.jfif).

## Verificação

- Testes verificam os caminhos relativos, URLs antigas, extensões mistas,
  campos vazios e prioridade do MySQL. Ausência e falha de conexão têm
  recuperação local, sem devolver detalhes internos ao visitante.
- A cobertura do catálogo foi comparada com o índice real do modelo: nenhuma
  classe ficou sem ficha no dump.
- A ficha do quero-quero foi renderizada localmente com dados reais do acervo.
  A foto `.jfif`, a morfologia `.jpg`, o reino `.jpg` e as figuras de filo/classe
  `.png` carregaram de verdade do novo R2 no navegador.
- As ferramentas HTTP automáticas receberam um bloqueio Cloudflare 1010. A
  abertura e a incorporação das imagens no navegador funcionaram; isso não foi
  tratado como prova de que o bucket estava desligado. Nenhuma configuração de
  segurança do Cloudflare foi alterada.
- Os 34 testes Python e a verificação de HTML no Node passaram. Os resultados
  de foto, vídeo e áudio foram conferidos em 320, 390 e 430 pixels, além de
  812 × 375 na horizontal. A coluna da taxonomia se ajusta também aos cartões
  internos menores dessas modalidades.
- A inferência dos modelos foi simulada nas prévias/testes. Os modelos, o MySQL
  e o servidor público não foram reiniciados nem receberam testes de carga.

## Ativação no PC servidor

Copie os arquivos do pacote atualizado, incluindo o catálogo comprimido em
`backend/data/`. Não substitua o `.env` nem as credenciais existentes. A variável
de imagens pode ser atualizada para a base acima; o Worker antigo conhecido
também é reconhecido automaticamente.

Para ativar a nova configuração e limpar o cache de fichas do processo, reinicie
somente o Python em um momento conveniente e recarregue a página. Mantenha o
túnel atual em execução: porta 5001 e rotas `/biovision/` foram preservadas.

O acesso real ao MySQL com as credenciais do PC servidor não foi verificado
nesta sessão. O catálogo local e a recuperação em caso de erro foram testados;
as fotos continuam dependendo da disponibilidade dos objetos públicos do R2.
