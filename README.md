# BioVision IFFar

Aplicacao Flask para identificacao de fauna em imagens, videos e audios, alem do processamento de videos aereos no BioVision Air.

## Estrutura

- `app.py`: entrada local, executada com `python app.py`.
- `backend/biovision_web/`: configuracao, banco, rotas e servicos de inferencia.
- `backend/data/`: indice de classes usado pelos modelos.
- `web/templates/`: paginas HTML.
- `web/static_biovision/`: CSS, JavaScript e recursos visuais.
- `models/`: modelos necessarios para inferencia, versionados com Git LFS.
- `database/`, `training/` e `docs/`: materiais auxiliares sem dados sensiveis.

## Execucao local

1. Instale o Git LFS e execute `git lfs pull` caso os modelos nao tenham sido baixados no clone.
2. Instale as dependencias com `pip install -r requirements.txt`.
3. Configure o MySQL e as demais variaveis em `.env` usando `.env.example` como referencia.
4. Execute `python app.py`.
5. Acesse `http://127.0.0.1:5001/biovision/`.

As imagens biologicas sao carregadas diretamente pelo navegador a partir de `BIOVISION_IMAGE_BASE_URL`. O MySQL armazena apenas os caminhos relativos dos arquivos.
