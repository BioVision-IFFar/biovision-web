# BioVision IFFar

Aplicação Flask para identificação de fauna em imagens, vídeos e áudios, além do processamento de vídeos aéreos no BioVision Air.

## Estrutura

- `app.py`: entrada local, executada com `python app.py`.
- `backend/biovision_web/`: configuração, banco, rotas e serviços de inferência.
- `backend/data/`: índice de classes usado pelos modelos.
- `web/templates/`: páginas HTML.
- `web/static_biovision/`: CSS, JavaScript e recursos visuais.
- `models/`: modelos necessários para inferência, versionados com Git LFS.
- `database/`, `training/` e `docs/`: materiais auxiliares sem dados sensíveis.

## Execução local

1. Instale o Git LFS e execute `git lfs pull` caso os modelos não tenham sido baixados no clone.
2. Instale as dependências com `pip install -r requirements.txt`.
3. Configure o MySQL e as demais variáveis em `.env` usando `.env.example` como referência.
4. Execute `python app.py`.
5. Acesse `http://127.0.0.1:5001/biovision/`.

As imagens biológicas são carregadas diretamente pelo navegador a partir de `BIOVISION_IMAGE_BASE_URL`. O MySQL armazena apenas os caminhos relativos dos arquivos.

## Gunicorn

Em Linux, execute o servidor de produção com:

```bash
gunicorn --config gunicorn.conf.py backend.wsgi:app
```

O Gunicorn usa um único processo com múltiplas threads porque a fila de análises fica em memória. Os modelos são carregados uma vez nesse processo antes de ele começar a atender requisições.

## Produção com uso reduzido de disco

O `requirements.txt` usa as distribuições oficiais CPU-only do PyTorch. Para evitar cache do `pip` e a cópia adicional dos modelos mantida pelo Git LFS, gere a imagem de produção com o `Dockerfile`:

```bash
docker build -t biovision-iffar .
docker run --env-file .env -p 5001:5001 biovision-iffar
```

O `.dockerignore` não envia `.git`, banco de desenvolvimento, documentação, treinamento ou arquivos temporários para a imagem. Os modelos necessários continuam incluídos uma única vez.
