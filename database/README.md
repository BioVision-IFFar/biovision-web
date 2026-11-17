# Banco de dados

O projeto usa o MySQL configurado pelas variaveis `BIOVISION_DB_*`. A tabela principal e `especie`; imagens sao caminhos de arquivos, nunca BLOB ou Base64.

O catálogo de recuperação `backend/data/species_catalog.json.gz` contém as 876
fichas do dump incluído no projeto. É usado quando o MySQL não está configurado,
falha ou não possui o registro/campo procurado. Dados preenchidos no MySQL têm
prioridade. A ficha humana complementar continua disponível localmente.

Para regenerar o catálogo depois de atualizar o dump confiável do projeto:

```powershell
py -3.11 scripts/export_species_catalog.py
```

O exportador lê valores textuais e `NULL` do arquivo; não executa SQL e não se
conecta ao banco. O arquivo comprimido fica na pasta privada do backend.

As imagens usam a base configurada em `BIOVISION_IMAGE_BASE_URL`, atualmente o
R2 público informado para o projeto. As subpastas `especies886/Especie`, `Reino`,
`Filo`, `Classe` e `Ordem`, assim como as extensões, são preservadas.

Dumps locais ficam ignorados pelo Git.
