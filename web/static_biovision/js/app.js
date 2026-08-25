/* ═══════════════════════════════════════════════
   Biovision IFFar — App Logic
   All original functionality preserved + new features
   ═══════════════════════════════════════════════ */

let videoStream;
let lastSelectedFile = null;
let airSelectedFile = null;
let identifyMode = 'photo';

document.documentElement.dataset.theme = 'dark';
try { localStorage.removeItem('theme'); } catch (e) {}

// Mapeamento de colunas → rótulos amigáveis
const colLabels = {
  especie: "Espécie",
  imagem: "Imagem",
  reino: "Reino",
  filo: "Filo",
  classe: "Classe",
  ordem: "Ordem",
  familia: "Família",
  genero: "Gênero",
  habitat: "Habitat",
  im_habitat: "Imagem do Habitat",
  morfologia: "Morfologia",
  im_morfologia: "Imagem da Morfologia",
  reproducao: "Reprodução",
  conservacao: "Conservação",
  im_conservacao: "Imagem da Conservação",
  carac_reino: "Características do Reino",
  im_reino: "Imagem do Reino",
  nomes: "Nomes Populares",
  carac_filo: "Características do Filo",
  im_filo: "Imagem do Filo",
  carac_classe: "Características da Classe",
  im_classe: "Imagem da Classe",
  carac_ordem: "Características da Ordem",
  im_ordem: "Imagem da Ordem"
};

const sectionIcons = {};

const $ = (sel) => document.querySelector(sel);

/* ══════════════════════════════
   FORCE TOP
   ══════════════════════════════ */
function forceTop() {
  if (document.activeElement && document.activeElement.blur) document.activeElement.blur();
  window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
  requestAnimationFrame(() => window.scrollTo(0, 0));
  setTimeout(() => window.scrollTo(0, 0), 0);
}

// Only force top on identification page
if (document.body.classList.contains('page-identify')) {
  window.addEventListener('load', forceTop);
  window.addEventListener('pageshow', (e) => { if (e.persisted) forceTop(); });
}

/* ══════════════════════════════
   LOADING STATE
   ══════════════════════════════ */
function showLoading(flag) {
  const loading = $("#loading");
  const btnIdentificar = $("#btnIdentificar");
  if (!loading) return;

  loading.style.display = flag ? "block" : "none";
  if (btnIdentificar) btnIdentificar.disabled = !!flag;

  if (flag) startLoadingFacts();
  else stopLoadingFacts();
}

function formatElapsedTime(seconds) {
  const total = Math.max(0, Math.floor(Number(seconds) || 0));
  const minutes = Math.floor(total / 60);
  const rest = total % 60;
  return minutes > 0 ? `${minutes}min ${String(rest).padStart(2, '0')}s` : `${rest}s`;
}

function progressElements(scope) {
  const isAir = scope === 'air';
  return {
    bar: document.getElementById(isAir ? 'airProgressBar' : 'analysisProgressBar'),
    value: document.getElementById(isAir ? 'airProgressValue' : 'analysisProgressValue'),
    elapsed: document.getElementById(isAir ? 'airElapsed' : 'analysisElapsed'),
    text: document.getElementById(isAir ? 'airLoadingText' : 'loadingText')
  };
}

function resetAnalysisProgress(scope) {
  const elements = progressElements(scope);
  if (elements.bar) {
    elements.bar.style.width = '0%';
    elements.bar.dataset.progress = '0';
  }
  if (elements.value) elements.value.textContent = '0%';
  if (elements.elapsed) elements.elapsed.textContent = 'Tempo decorrido: 0s';
}

function updateAnalysisProgress(scope, state = {}) {
  const elements = progressElements(scope);
  const incoming = Math.max(0, Math.min(100, Number(state.progress) || 0));
  const previous = Number(elements.bar?.dataset.progress || 0);
  const progress = state.status === 'completed' ? 100 : Math.max(previous, incoming);
  let stage = state.stage || 'Processando arquivo';
  if (state.status === 'queued' && state.queue_position) {
    stage = `Aguardando processamento - posição ${state.queue_position} na fila`;
  }

  if (elements.bar) {
    elements.bar.style.width = `${progress}%`;
    elements.bar.dataset.progress = String(progress);
  }
  if (elements.value) elements.value.textContent = `${Math.round(progress)}%`;
  if (elements.elapsed) {
    elements.elapsed.textContent = `Tempo decorrido: ${formatElapsedTime(state.elapsed_seconds)}`;
  }
  if (elements.text) elements.text.textContent = stage;
}

function createAnalysisJob(endpoint, formData, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const separator = endpoint.includes('?') ? '&' : '?';
    xhr.open('POST', `${endpoint}${separator}async=1`);
    xhr.responseType = 'json';
    xhr.timeout = 120000;
    xhr.setRequestHeader('X-BioVision-Async', '1');

    xhr.upload.addEventListener('progress', event => {
      if (!event.lengthComputable) return;
      const uploadProgress = Math.max(1, Math.min(8, Math.round((event.loaded / event.total) * 8)));
      onProgress?.({
        progress: uploadProgress,
        stage: 'Enviando arquivo',
        elapsed_seconds: 0,
        status: 'uploading'
      });
    });

    xhr.addEventListener('load', () => {
      let payload = xhr.response;
      if (!payload) {
        try { payload = JSON.parse(xhr.responseText || '{}'); } catch (error) { payload = null; }
      }
      if (xhr.status < 200 || xhr.status >= 300) {
        reject(new Error(payload?.erro || 'O servidor não conseguiu iniciar a análise.'));
        return;
      }
      resolve(payload || {});
    });
    xhr.addEventListener('timeout', () => reject(new Error('O envio demorou demais. Verifique a conexão e tente novamente.')));
    xhr.addEventListener('error', () => reject(new Error('Falha ao comunicar com o servidor.')));
    xhr.send(formData);
  });
}

const wait = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));

async function runAnalysisJob(endpoint, formData, onProgress) {
  const created = await createAnalysisJob(endpoint, formData, onProgress);
  if (!created.job_id || !created.status_url) return created;

  onProgress?.({
    progress: 8,
    stage: 'Arquivo recebido pelo servidor',
    elapsed_seconds: 0,
    status: 'queued'
  });

  let connectionFailures = 0;
  while (true) {
    await wait(700);
    let response;
    try {
      response = await fetch(created.status_url, {
        method: 'GET',
        cache: 'no-store',
        headers: { 'Accept': 'application/json' }
      });
      connectionFailures = 0;
    } catch (error) {
      connectionFailures += 1;
      if (connectionFailures >= 8) {
        throw new Error('A conexão com o servidor foi interrompida durante a análise.');
      }
      onProgress?.({
        progress: 8,
        stage: 'Reconectando ao servidor',
        elapsed_seconds: 0,
        status: 'running'
      });
      await wait(Math.min(3500, connectionFailures * 500));
      continue;
    }

    const state = await response.json().catch(() => null);
    if (!response.ok || !state) {
      throw new Error(state?.erro || 'O servidor retornou uma resposta inválida.');
    }
    onProgress?.(state);
    if (state.status === 'completed') return state.result || {};
    if (state.status === 'failed') throw new Error(state.error || 'Não foi possível concluir a análise.');
  }
}

/* ══════════════════════════════
   LOADING FACTS (curiosidades)
   ══════════════════════════════ */
const bioFacts = [];

let factsInterval = null;
function startLoadingFacts() {
  const el = document.getElementById('loadingFacts');
  if (!el) return;
  let idx = Math.floor(Math.random() * bioFacts.length);
  el.style.opacity = '1';
  el.textContent = bioFacts[idx];
  factsInterval = setInterval(() => {
    el.style.opacity = '0';
    setTimeout(() => {
      idx = (idx + 1) % bioFacts.length;
      el.textContent = bioFacts[idx];
      el.style.opacity = '1';
    }, 350);
  }, 3500);
}

function stopLoadingFacts() {
  if (factsInterval) clearInterval(factsInterval);
  factsInterval = null;
  const el = document.getElementById('loadingFacts');
  if (el) { el.textContent = ''; el.style.opacity = '0'; }
}

/* ══════════════════════════════
   IMAGE COMPRESSION
   ══════════════════════════════ */
function dataURLtoBlob(dataUrl) {
  const arr = dataUrl.split(','), mime = arr[0].match(/:(.*?);/)[1];
  const bstr = atob(arr[1]); let n = bstr.length; const u8arr = new Uint8Array(n);
  while (n--) u8arr[n] = bstr.charCodeAt(n);
  return new Blob([u8arr], { type: mime });
}

function resizeImageFile(file, maxSize = 1024, quality = 0.9) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    const reader = new FileReader();
    reader.onload = e => { img.src = e.target.result; };
    reader.onerror = reject;
    img.onload = () => {
      const canvas = document.createElement("canvas");
      let { width, height } = img;
      const scale = Math.min(1, maxSize / Math.max(width, height));
      width = Math.round(width * scale);
      height = Math.round(height * scale);
      canvas.width = width;
      canvas.height = height;
      canvas.getContext("2d").drawImage(img, 0, 0, width, height);
      resolve(dataURLtoBlob(canvas.toDataURL("image/jpeg", quality)));
    };
    img.onerror = reject;
    reader.readAsDataURL(file);
  });
}

/* ══════════════════════════════
   SEND IMAGE
   ══════════════════════════════ */
async function enviarImagemArquivo(file) {
  const formData = new FormData();

  let endpoint = "/biovision/classificar";
  if (identifyMode === 'photo') {
    try {
      const blob = await resizeImageFile(file);
      formData.append("imagem", blob, "upload.jpg");
    } catch {
      formData.append("imagem", file);
    }
  } else if (identifyMode === 'video') {
    endpoint = "/biovision/classificar-video";
    formData.append("video", file, file.name || "video.mp4");
  } else {
    endpoint = "/biovision/classificar-audio";
    formData.append("audio", file, file.name || "audio.wav");
  }

  try {
    resetAnalysisProgress('identify');
    showLoading(true);
    const loadingText = document.getElementById('loadingText');
    if (loadingText) {
      loadingText.textContent = identifyMode === 'photo'
        ? 'Identificando espécie e buscando dados...'
        : identifyMode === 'video'
          ? 'Analisando vídeo e recortes da espécie...'
          : 'Analisando assinatura sonora...';
    }
    const dados = await runAnalysisJob(
      endpoint,
      formData,
      state => updateAnalysisProgress('identify', state)
    );
    if (identifyMode === 'photo') exibirResultado(dados);
    else exibirResultadoMidia(dados);
    if (!dados.erro) incrementCounter();
  } catch (err) {
    const errorPayload = { erro: err.message || "Falha ao comunicar com o servidor." };
    if (identifyMode === 'photo') exibirResultado(errorPayload);
    else exibirResultadoMidia(errorPayload);
  } finally {
    showLoading(false);
  }
}

/* ══════════════════════════════
   SMOOTH SCROLL TO TOP
   ══════════════════════════════ */
function scrollToTopEaseOut(duration = 850) {
  if (window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
    window.scrollTo(0, 0);
    return;
  }

  const startY = window.pageYOffset || document.documentElement.scrollTop || 0;
  if (startY <= 0) return;

  const start = performance.now();
  const easeOutQuint = t => 1 - Math.pow(1 - t, 5);
  let stopped = false;
  const stop = () => { stopped = true; };

  window.addEventListener('wheel', stop, { once: true, passive: true });
  window.addEventListener('touchstart', stop, { once: true, passive: true });
  window.addEventListener('keydown', stop, { once: true });

  function step(now) {
    if (stopped) return;
    const t = Math.min(1, (now - start) / duration);
    window.scrollTo(0, Math.round(startY * (1 - easeOutQuint(t))));
    if (t < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

/* ══════════════════════════════
   IMAGE PREVIEW
   ══════════════════════════════ */
function mostrarImagem(input) {
  const file = input.files[0];
  if (!file) return;

  lastSelectedFile = file;

  if (identifyMode !== 'photo') {
    const dropTitle = document.querySelector('#uploadDropzone .upload-title');
    const dropSubtitle = document.querySelector('#uploadDropzone .upload-subtitle');
    const dropFormats = document.querySelector('#uploadDropzone .upload-formats');
    const readable = identifyMode === 'video' ? 'vídeo' : 'áudio ou vídeo';
    if (dropTitle) dropTitle.textContent = file.name || `Arquivo de ${readable} selecionado`;
    if (dropSubtitle) dropSubtitle.textContent = 'Arquivo pronto para processamento';
    if (dropFormats) dropFormats.textContent = `${formatFileSize(file.size)} selecionado`;
    const preview = document.getElementById('imagemPreview');
    if (preview) preview.style.display = 'none';
    $("#btnIdentificar").style.display = "block";
    return;
  }

  const reader = new FileReader();
  reader.onload = e => {
    $("#previewImagem").src = e.target.result;
    $("#imagemPreview").style.display = "block";

    // Hide dropzone elements
    const dropzone = $("#uploadDropzone");
    if (dropzone) dropzone.style.display = "none";
  };
  reader.readAsDataURL(file);

  $("#btnIdentificar").style.display = "block";
}

/* ══════════════════════════════
   CAMERA (preserved)
   ══════════════════════════════ */
function abrirCamera() {
  const fg = $("#formGaleria");
  const fc = $("#formCamera");
  if (fg) fg.style.display = "none";
  if (fc) fc.style.display = "block";

  navigator.mediaDevices.getUserMedia({ video: true })
    .then(stream => {
      videoStream = stream;
      const v = $("#video");
      if (v) v.srcObject = stream;
    })
    .catch(err => alert("Erro ao acessar câmera: " + err));
}

function tirarFoto() {
  const video = $("#video");
  if (!video) return;
  const canvas = document.createElement("canvas");
  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

  const dataUrl = canvas.toDataURL("image/png");
  $("#previewImagem").src = dataUrl;
  $("#imagemPreview").style.display = "block";

  canvas.toBlob(blob => {
    lastSelectedFile = blob;
    $("#btnIdentificar").style.display = "block";
  }, "image/png");
}

/* ══════════════════════════════
   BACK TO TOP BUTTON
   ══════════════════════════════ */
function addBackToTopButton() {
  const resultadoDiv = document.getElementById("resultado");
  if (!resultadoDiv) return;

  const old = document.getElementById("toTopContainer");
  if (old) old.remove();

  const wrap = document.createElement("div");
  wrap.id = "toTopContainer";
  wrap.className = "to-top-container";

  const btn = document.createElement("button");
  btn.type = "button";
  btn.className = "btn-fab";
  btn.setAttribute("aria-label", "Voltar ao topo");
  btn.title = "Voltar ao topo";
  btn.textContent = "↑";
  btn.addEventListener("click", () => scrollToTopEaseOut(900));

  wrap.appendChild(btn);
  resultadoDiv.appendChild(wrap);
}

/* ══════════════════════════════
   GET ICON FOR SECTION
   ══════════════════════════════ */
function getIcon(label) {
  return '';
}

/* ══════════════════════════════
   CHECK IF VALUE HAS REAL CONTENT
   ══════════════════════════════ */
function hasContent(val) {
  if (val === null || val === undefined) return false;
  if (typeof val === 'string') {
    const trimmed = val.trim();
    if (trimmed === '' || trimmed === 'null' || trimmed === 'None' || trimmed === 'N/A') return false;
    return true;
  }
  return Boolean(val);
}

function hasImage(val) {
  if (!val || typeof val !== 'string') return false;
  const trimmed = val.trim();
  return /^https?:\/\//i.test(trimmed);
}

function imgSrc(imageUrl) {
  return hasImage(imageUrl) ? escapeHtml(imageUrl.trim()) : '';
}

/* ══════════════════════════════
   DISPLAY RESULTS
   ══════════════════════════════ */
function exibirResultado(dados) {
  const resultadoDiv = $("#resultado");
  if (!resultadoDiv) return;

  if (dados.erro) {
    resultadoDiv.innerHTML = `<div class="card card-animate"><p style="color:#e53935;font-weight:600;">${escapeHtml(dados.erro)}</p></div>`;
    const btnNova = document.getElementById("btnNova");
    if (btnNova) btnNova.style.display = "block";
    setTimeout(() => resultadoDiv.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    return;
  }

  const especie = dados.dados_taxon || {};

  // ─── HEADER CARD: imagem + identificação ───
  const mainImageTitle = hasContent(especie.title_imagem) ? especie.title_imagem : 'Imagem da espécie';
  const headerImagem = hasImage(especie.imagem)
    ? `<img src="${imgSrc(especie.imagem)}" class="result-header-img" alt="${escapeHtml(mainImageTitle)}" title="${escapeHtml(mainImageTitle)}"/>`
    : `<div class="result-header-img result-header-noimg">Sem imagem disponível</div>`;

  const nomeCientifico = especie.especie || dados.keyword;
  const nomesPopulares = hasContent(especie.nomes) ? especie.nomes : '';

  const headerHTML = `
    <div class="result-header card-animate">
      <div class="result-header-imgwrap">
        ${headerImagem}
      </div>
      <div class="result-header-info">
        <p class="result-header-label">Espécie identificada</p>
        <h2 class="result-header-name"><i>${nomeCientifico}</i></h2>
        ${nomesPopulares ? `<p class="result-header-popular">${nomesPopulares}</p>` : ''}
        <div class="result-header-confidence">
          <span class="conf-dot"></span>
          ${Number(dados.confidence).toFixed(1)}% de confiança
        </div>
      </div>
    </div>
  `;

  // ─── TABS: monta as abas baseado no que tem dados ───
  const tabs = [];

  // Aba 1: Taxonomia (sempre tenta mostrar)
  const ordemTaxonomia = ["reino", "filo", "classe", "ordem", "familia", "genero", "especie"];
  const taxoItems = [];
  ordemTaxonomia.forEach(chave => {
    if (hasContent(especie[chave])) {
      let valor = especie[chave];
      if (chave === "especie") valor = `<i>${valor}</i>`;
      taxoItems.push(`<div class="taxo-row"><span class="taxo-label">${colLabels[chave]}</span><span class="taxo-value">${valor}</span></div>`);
    }
  });
  if (taxoItems.length) {
    tabs.push({
      id: 'taxonomia',
      label: 'Taxonomia',
      content: `<div class="taxo-grid">${taxoItems.join('')}</div>`
    });
  }

  // Aba 2: Habitat
  const habitatParts = [];
  if (hasContent(especie.habitat)) habitatParts.push(`<p>${especie.habitat}</p>`);
  if (habitatParts.length) {
    tabs.push({ id: 'habitat', label: 'Habitat', content: habitatParts.join('') });
  }

  // Aba 3: Morfologia
  const morfoParts = [];
  if (hasContent(especie.morfologia)) morfoParts.push(`<p>${especie.morfologia}</p>`);
  if (hasImage(especie.im_morfologia)) {
    const title = hasContent(especie.title_im_morfologia) ? especie.title_im_morfologia : 'Morfologia';
    morfoParts.push(`<img src="${imgSrc(especie.im_morfologia)}" class="tab-img" alt="${escapeHtml(title)}" title="${escapeHtml(title)}"/>`);
  }
  if (morfoParts.length) {
    tabs.push({ id: 'morfologia', label: 'Morfologia', content: morfoParts.join('') });
  }

  // Aba 4: Reprodução
  if (hasContent(especie.reproducao)) {
    tabs.push({ id: 'reproducao', label: 'Reprodução', content: `<p>${especie.reproducao}</p>` });
  }

  // Aba 5: Conservação
  const consParts = [];
  if (hasContent(especie.conservacao)) consParts.push(`<p>${especie.conservacao}</p>`);
  if (hasImage(especie.im_conservacao)) consParts.push(`<img src="${imgSrc(especie.im_conservacao)}" class="tab-img" alt="Conservação"/>`);
  if (consParts.length) {
    tabs.push({ id: 'conservacao', label: 'Conservação', content: consParts.join('') });
  }

  // Aba 6: Características adicionais (filo, classe, ordem)
  const caracParts = [];
  const caracMap = [
    { chave: 'carac_reino', img: 'im_reino', label: 'Reino' },
    { chave: 'carac_filo', img: 'im_filo', label: 'Filo' },
    { chave: 'carac_classe', img: 'im_classe', label: 'Classe' },
    { chave: 'carac_ordem', img: 'im_ordem', label: 'Ordem' }
  ];
  caracMap.forEach(c => {
    if (hasContent(especie[c.chave])) {
      caracParts.push(`<h4 class="tab-subhead">${c.label}</h4><p>${especie[c.chave]}</p>`);
      if (hasImage(especie[c.img])) {
        caracParts.push(`<img src="${imgSrc(especie[c.img])}" class="tab-img" alt="${c.label}"/>`);
      }
    }
  });
  if (caracParts.length) {
    tabs.push({ id: 'caracteristicas', label: 'Características', content: caracParts.join('') });
  }

  // ─── Monta HTML das abas ───
  let tabsHTML = '';
  if (tabs.length) {
    const tabBtns = tabs.map((t, i) =>
      `<button class="tab-btn ${i === 0 ? 'is-active' : ''}" data-tab="${t.id}">${t.label}</button>`
    ).join('');

    const tabPanels = tabs.map((t, i) =>
      `<div class="tab-panel ${i === 0 ? 'is-active' : ''}" data-panel="${t.id}">${t.content}</div>`
    ).join('');

    tabsHTML = `
      <div class="result-tabs card-animate" style="animation-delay:120ms;">
        <div class="tab-bar-wrap">
          <div class="tab-bar">${tabBtns}</div>
        </div>
        <div class="tab-content">${tabPanels}</div>
      </div>
    `;
  }

  // ─── Render ───
  resultadoDiv.innerHTML = headerHTML + tabsHTML;

  // Liga clicks nas abas
  const btns = resultadoDiv.querySelectorAll('.tab-btn');
  const panels = resultadoDiv.querySelectorAll('.tab-panel');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.tab;
      btns.forEach(b => b.classList.toggle('is-active', b === btn));
      panels.forEach(p => p.classList.toggle('is-active', p.dataset.panel === id));
      // Garante que a aba clicada fique visível
      btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    });
  });

  // Detecta overflow horizontal pra mostrar/esconder fade nas bordas
  const tabBar = resultadoDiv.querySelector('.tab-bar');
  const tabWrap = resultadoDiv.querySelector('.tab-bar-wrap');
  if (tabBar && tabWrap) {
    const updateOverflow = () => {
      const hasRight = tabBar.scrollLeft + tabBar.clientWidth < tabBar.scrollWidth - 2;
      const hasLeft = tabBar.scrollLeft > 2;
      tabWrap.classList.toggle('has-overflow-right', hasRight);
      tabWrap.classList.toggle('has-overflow-left', hasLeft);
    };
    tabBar.addEventListener('scroll', updateOverflow, { passive: true });
    window.addEventListener('resize', updateOverflow);
    // Roda uma vez depois do render
    setTimeout(updateOverflow, 50);
  }

  // Liga lightbox em todas as imagens (header + abas)
  const headerImg = resultadoDiv.querySelector('.result-header-imgwrap img');
  if (headerImg) {
    headerImg.parentElement.addEventListener('click', () => openLightbox(headerImg.src));
  }
  resultadoDiv.querySelectorAll('.tab-img').forEach(img => {
    img.addEventListener('click', () => openLightbox(img.src));
  });

  // Foco no header pra acessibilidade
  const header = resultadoDiv.querySelector('.result-header');
  if (header) { header.setAttribute('tabindex', '-1'); header.focus(); }

  const btnNova = document.getElementById("btnNova");
  if (btnNova) btnNova.style.display = "block";
}

function renderTaxonDetailHtml(dados) {
  const especie = dados.dados_taxon || {};
  const nomeCientifico = especie.especie || dados.keyword || dados.label || dados.raw_label || 'Espécie identificada';
  const nomesPopulares = hasContent(especie.nomes) ? especie.nomes : '';
  const confidence = Number(dados.confidence || 0);

  if (!dados.dados_taxon) {
    return `
      <div class="media-empty-detail">
        <p class="result-header-label">Ficha da espécie</p>
        <h3><i>${escapeHtml(nomeCientifico)}</i></h3>
        <p>Essa espécie foi identificada, mas ainda não existe ficha completa no banco local.</p>
      </div>
    `;
  }

  const mainImageTitle = hasContent(especie.title_imagem) ? especie.title_imagem : 'Imagem da espécie';
  const headerImagem = hasImage(especie.imagem)
    ? `<img src="${imgSrc(especie.imagem)}" class="result-header-img" alt="${escapeHtml(mainImageTitle)}" title="${escapeHtml(mainImageTitle)}"/>`
    : `<div class="result-header-img result-header-noimg">Sem imagem disponível</div>`;

  const headerHTML = `
    <div class="result-header media-detail-header">
      <div class="result-header-imgwrap">
        ${headerImagem}
      </div>
      <div class="result-header-info">
        <p class="result-header-label">Ficha completa</p>
        <h2 class="result-header-name"><i>${nomeCientifico}</i></h2>
        ${nomesPopulares ? `<p class="result-header-popular">${nomesPopulares}</p>` : ''}
        <div class="result-header-confidence">
          <span class="conf-dot"></span>
          ${confidence.toFixed(1)}% de confiança
        </div>
      </div>
    </div>
  `;

  const tabs = [];
  const ordemTaxonomia = ["reino", "filo", "classe", "ordem", "familia", "genero", "especie"];
  const taxoItems = [];
  ordemTaxonomia.forEach(chave => {
    if (hasContent(especie[chave])) {
      let valor = especie[chave];
      if (chave === "especie") valor = `<i>${valor}</i>`;
      taxoItems.push(`<div class="taxo-row"><span class="taxo-label">${colLabels[chave]}</span><span class="taxo-value">${valor}</span></div>`);
    }
  });
  if (taxoItems.length) tabs.push({ id: 'taxonomia', label: 'Taxonomia', content: `<div class="taxo-grid">${taxoItems.join('')}</div>` });

  const habitatParts = [];
  if (hasContent(especie.habitat)) habitatParts.push(`<p>${especie.habitat}</p>`);
  if (habitatParts.length) tabs.push({ id: 'habitat', label: 'Habitat', content: habitatParts.join('') });

  const morfoParts = [];
  if (hasContent(especie.morfologia)) morfoParts.push(`<p>${especie.morfologia}</p>`);
  if (hasImage(especie.im_morfologia)) {
    const title = hasContent(especie.title_im_morfologia) ? especie.title_im_morfologia : 'Morfologia';
    morfoParts.push(`<img src="${imgSrc(especie.im_morfologia)}" class="tab-img" alt="${escapeHtml(title)}" title="${escapeHtml(title)}"/>`);
  }
  if (morfoParts.length) tabs.push({ id: 'morfologia', label: 'Morfologia', content: morfoParts.join('') });

  if (hasContent(especie.reproducao)) tabs.push({ id: 'reproducao', label: 'Reprodução', content: `<p>${especie.reproducao}</p>` });

  const consParts = [];
  if (hasContent(especie.conservacao)) consParts.push(`<p>${especie.conservacao}</p>`);
  if (hasImage(especie.im_conservacao)) consParts.push(`<img src="${imgSrc(especie.im_conservacao)}" class="tab-img" alt="Conservação"/>`);
  if (consParts.length) tabs.push({ id: 'conservacao', label: 'Conservação', content: consParts.join('') });

  const caracParts = [];
  [
    { chave: 'carac_reino', img: 'im_reino', label: 'Reino' },
    { chave: 'carac_filo', img: 'im_filo', label: 'Filo' },
    { chave: 'carac_classe', img: 'im_classe', label: 'Classe' },
    { chave: 'carac_ordem', img: 'im_ordem', label: 'Ordem' }
  ].forEach(c => {
    if (hasContent(especie[c.chave])) {
      caracParts.push(`<h4 class="tab-subhead">${c.label}</h4><p>${especie[c.chave]}</p>`);
      if (hasImage(especie[c.img])) caracParts.push(`<img src="${imgSrc(especie[c.img])}" class="tab-img" alt="${c.label}"/>`);
    }
  });
  if (caracParts.length) tabs.push({ id: 'caracteristicas', label: 'Características', content: caracParts.join('') });

  if (!tabs.length) return headerHTML;

  const tabBtns = tabs.map((t, i) =>
    `<button class="tab-btn ${i === 0 ? 'is-active' : ''}" data-tab="${t.id}">${t.label}</button>`
  ).join('');
  const tabPanels = tabs.map((t, i) =>
    `<div class="tab-panel ${i === 0 ? 'is-active' : ''}" data-panel="${t.id}">${t.content}</div>`
  ).join('');

  return `
    ${headerHTML}
    <div class="result-tabs media-detail-tabs">
      <div class="tab-bar-wrap">
        <div class="tab-bar">${tabBtns}</div>
      </div>
      <div class="tab-content">${tabPanels}</div>
    </div>
  `;
}

function ativarDetalheTaxon(container) {
  if (!container) return;
  const btns = container.querySelectorAll('.tab-btn');
  const panels = container.querySelectorAll('.tab-panel');
  btns.forEach(btn => {
    btn.addEventListener('click', () => {
      const id = btn.dataset.tab;
      btns.forEach(b => b.classList.toggle('is-active', b === btn));
      panels.forEach(p => p.classList.toggle('is-active', p.dataset.panel === id));
      btn.scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'center' });
    });
  });

  const headerImg = container.querySelector('.result-header-imgwrap img');
  if (headerImg) headerImg.parentElement.addEventListener('click', () => openLightbox(headerImg.src));
  container.querySelectorAll('.tab-img').forEach(img => {
    img.addEventListener('click', () => openLightbox(img.src));
  });
}

function exibirResultadoMidia(dados) {
  const resultadoDiv = $("#resultado");
  if (!resultadoDiv) return;

  if (dados.erro && !Array.isArray(dados.top_species) && !Array.isArray(dados.species_found)) {
    resultadoDiv.innerHTML = `<div class="card card-animate"><p style="color:#e53935;font-weight:600;">${escapeHtml(dados.erro)}</p></div>`;
    const btnNova = document.getElementById("btnNova");
    if (btnNova) btnNova.style.display = "block";
    setTimeout(() => resultadoDiv.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
    return;
  }

  if (dados.mode === 'video_species') {
    const species = Array.isArray(dados.species_found) ? dados.species_found : [];
    const cards = species.length ? species.map(item => `
      <div class="media-result-item">
        <span>${escapeHtml(item.yolo_label || 'Classe')}</span>
        <strong><i>${escapeHtml(item.selector_label || item.label || 'Não identificado')}</i></strong>
        <small>${Number(item.confidence || 0).toFixed(2)}% de confiança - ${Number(item.frames_detected || 0)} frame(s)</small>
      </div>
    `).join('') : `
      <div class="media-result-item">
        <span>Resultado</span>
        <strong>Nenhuma espécie identificada</strong>
        <small>O vídeo foi processado, mas não houve detecção suficiente.</small>
      </div>
    `;

    const selector = species.length ? `
      <label class="media-species-picker">
        <span>Ver informações de</span>
        <select id="mediaSpeciesSelect" aria-label="Escolher espécie identificada">
          ${species.map((item, index) => `
            <option value="${index}">${escapeHtml(item.selector_label || item.label || item.raw_label || `Espécie ${index + 1}`)}</option>
          `).join('')}
        </select>
      </label>
    ` : '';

    resultadoDiv.innerHTML = `
      <div class="media-result-card card-animate">
        <p class="result-header-label">Identificação por vídeo</p>
        <h2>${escapeHtml(dados.summary_text || 'Processamento concluído')}</h2>
        <div class="media-result-stats">
          <div><span>Tempo do vídeo</span><strong>${Number(dados.duracao || 0).toFixed(2)}s</strong></div>
          <div><span>Classes detectadas</span><strong>${Number(dados.summary?.classes_detected || species.length)}</strong></div>
          <div><span>Espécies listadas</span><strong>${species.length}</strong></div>
        </div>
        <div class="media-result-grid">${cards}</div>
        ${selector}
        <div id="mediaTaxonDetail" class="media-taxon-detail"></div>
      </div>
    `;

    const detail = resultadoDiv.querySelector('#mediaTaxonDetail');
    const select = resultadoDiv.querySelector('#mediaSpeciesSelect');
    const renderSelected = (index) => {
      if (!detail || !species.length) return;
      const item = species[index] || species[0];
      detail.innerHTML = renderTaxonDetailHtml({
        keyword: item.label,
        raw_keyword: item.raw_label,
        confidence: item.confidence,
        dados_taxon: item.dados_taxon
      });
      ativarDetalheTaxon(detail);
    };
    renderSelected(0);
    if (select) {
      select.addEventListener('change', () => renderSelected(Number(select.value || 0)));
    }
  } else {
    const top = Array.isArray(dados.top_species) ? dados.top_species.slice(0, 1) : [];
    const best = top[0] || null;
    const confidenceScale = (value) => {
      const number = Number(value || 0);
      return number > 1 ? number : number * 100;
    };
    const audioCard = best ? `
      <div class="media-result-item">
        <span>Identificado</span>
        <strong><i>${escapeHtml(best.selector_label || best.label || best.species || 'Identificado')}</i></strong>
        <small>${confidenceScale(best.confidence).toFixed(2)}% de confiança</small>
      </div>
    ` : '';

    resultadoDiv.innerHTML = `
      <div class="media-result-card card-animate">
        <p class="result-header-label">Identificação por áudio</p>
        <h2>${escapeHtml(dados.summary_text || dados.keyword || 'Áudio analisado')}</h2>
        <div class="media-result-stats media-result-stats-audio">
          <div><span>Confiança</span><strong>${Number(dados.confidence || 0).toFixed(2)}%</strong></div>
          <div><span>Tempo do áudio</span><strong>${Number(dados.duration_seconds || 0).toFixed(2)}s</strong></div>
        </div>
        ${dados.erro ? `<p class="media-result-note">${escapeHtml(dados.reason || dados.erro)}</p>` : ''}
        ${audioCard ? `<div class="media-result-grid media-result-grid-single">${audioCard}</div>` : ''}
        <div id="mediaTaxonDetail" class="media-taxon-detail"></div>
      </div>
    `;
    const detail = resultadoDiv.querySelector('#mediaTaxonDetail');
    if (detail && best) {
      detail.innerHTML = renderTaxonDetailHtml({
        keyword: best.selector_label || best.label,
        raw_keyword: best.species,
        confidence: confidenceScale(best.confidence),
        dados_taxon: best.dados_taxon
      });
      ativarDetalheTaxon(detail);
    }
  }

  const btnNova = document.getElementById("btnNova");
  if (btnNova) btnNova.style.display = "block";
  setTimeout(() => resultadoDiv.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
}

/* ══════════════════════════════
   IMAGE LIGHTBOX
   ══════════════════════════════ */
function openLightbox(src) {
  let lb = document.getElementById('imgLightbox');
  if (!lb) {
    lb = document.createElement('div');
    lb.id = 'imgLightbox';
    lb.className = 'img-lightbox';
    lb.innerHTML = `
      <button class="img-lightbox-close" aria-label="Fechar">×</button>
      <img src="" alt="Imagem ampliada"/>
    `;
    document.body.appendChild(lb);

    // Click no fundo ou no botão fecha
    lb.addEventListener('click', (e) => {
      if (e.target === lb || e.target.classList.contains('img-lightbox-close')) {
        closeLightbox();
      }
    });

    // ESC fecha
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') closeLightbox();
    });
  }
  lb.querySelector('img').src = src;
  lb.classList.add('is-open');
  document.body.style.overflow = 'hidden';
}

function closeLightbox() {
  const lb = document.getElementById('imgLightbox');
  if (lb) {
    lb.classList.remove('is-open');
    document.body.style.overflow = '';
  }
}

/* ══════════════════════════════
   COUNTER
   ══════════════════════════════ */
function getCount() {
  try { return parseInt(localStorage.getItem('biovision_count') || '0', 10); }
  catch { return 0; }
}

function incrementCounter() {
  try {
    const c = getCount() + 1;
    localStorage.setItem('biovision_count', c.toString());
    animateCounter(c);
  } catch (e) { }
}

function animateCounter(target) {
  const el = document.getElementById('statCounter');
  if (!el) return;
  const start = parseInt(el.textContent, 10) || 0;
  const diff = target - start;
  if (diff <= 0) { el.textContent = target; return; }
  const duration = 600;
  const t0 = performance.now();
  function tick(now) {
    const p = Math.min(1, (now - t0) / duration);
    el.textContent = Math.round(start + diff * (1 - Math.pow(1 - p, 3)));
    if (p < 1) requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
}

/* ══════════════════════════════
   SCROLL REVEAL
   ══════════════════════════════ */
function initScrollReveal() {
  const sections = document.querySelectorAll('.fade-in-section');
  if (!sections.length) return;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting) entry.target.classList.add('is-visible');
    });
  }, { threshold: 0.05, rootMargin: '0px 0px -10px 0px' });

  sections.forEach(s => observer.observe(s));
}

function initLandingHero() {
  const hero = document.querySelector('.bv-home-hero');
  if (!hero) return;

  requestAnimationFrame(() => hero.classList.add('is-ready'));
}

function initFloatingHeader() {
  const nav = document.querySelector('.nav');
  if (!nav) return;

  const enterAt = 36;
  const leaveAt = 10;
  let isCompact = window.scrollY >= enterAt;
  let animationFrame = null;

  const update = () => {
    const scrollTop = Math.max(window.scrollY || document.documentElement.scrollTop || 0, 0);
    const shouldCompact = isCompact ? scrollTop > leaveAt : scrollTop >= enterAt;

    if (shouldCompact !== isCompact) {
      isCompact = shouldCompact;
      nav.classList.toggle('is-scrolled', isCompact);
    }

    animationFrame = null;
  };

  const requestUpdate = () => {
    if (animationFrame) return;
    animationFrame = requestAnimationFrame(update);
  };

  window.addEventListener('scroll', requestUpdate, { passive: true });
  window.addEventListener('pageshow', requestUpdate);
  nav.classList.toggle('is-scrolled', isCompact);
  update();
}

/* ══════════════════════════════
   DRAG & DROP
   ══════════════════════════════ */
function initDragDrop() {
  const box = document.getElementById('uploadBox');
  if (!box) return;

  const prevent = (e) => { e.preventDefault(); e.stopPropagation(); };

  // Also allow clicking the dropzone to select
  const dropzone = document.getElementById('uploadDropzone');
  if (dropzone) {
    dropzone.addEventListener('click', () => {
      document.getElementById('imagem').click();
    });
  }

  box.addEventListener('dragenter', (e) => { prevent(e); box.classList.add('drag-over'); });
  box.addEventListener('dragover', (e) => { prevent(e); box.classList.add('drag-over'); });
  box.addEventListener('dragleave', (e) => { prevent(e); box.classList.remove('drag-over'); });
  box.addEventListener('drop', (e) => {
    prevent(e);
    box.classList.remove('drag-over');
    const files = e.dataTransfer.files;
    const file = files[0];
    const acceptsMode =
      identifyMode === 'photo' ? file?.type.startsWith('image/') :
      identifyMode === 'video' ? file?.type.startsWith('video/') :
      file?.type.startsWith('audio/') || /\.(mp3|wav|m4a|ogg|webm|mp4|mov)$/i.test(file?.name || '');

    if (files.length > 0 && acceptsMode) {
      const input = document.getElementById('imagem');
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      mostrarImagem(input);
    }
  });
}

/* ══════════════════════════════
   NEW IDENTIFICATION (reset)
   ══════════════════════════════ */
function resetIdentification() {
  lastSelectedFile = null;
  showLoading(false);
  resetAnalysisProgress('identify');

  const preview = document.getElementById('imagemPreview');
  if (preview) preview.style.display = 'none';

  const previewImg = document.getElementById('previewImagem');
  if (previewImg) previewImg.src = '';

  const btnId = document.getElementById('btnIdentificar');
  if (btnId) btnId.style.display = 'none';

  const btnNova = document.getElementById('btnNova');
  if (btnNova) btnNova.style.display = 'none';

  const resultado = document.getElementById('resultado');
  if (resultado) resultado.innerHTML = '';

  const dropzone = document.getElementById('uploadDropzone');
  if (dropzone) dropzone.style.display = '';

  const input = document.getElementById('imagem');
  if (input) input.value = '';

  const selectLabel = document.getElementById('imageSelectLabel');
  const dropTitle = document.querySelector('#uploadDropzone .upload-title');
  const dropSubtitle = document.querySelector('#uploadDropzone .upload-subtitle');
  const dropFormats = document.querySelector('#uploadDropzone .upload-formats');
  if (identifyMode === 'photo') {
    if (selectLabel) selectLabel.textContent = 'Selecionar imagem';
    if (dropTitle) dropTitle.textContent = 'Arraste sua imagem aqui';
    if (dropSubtitle) dropSubtitle.textContent = 'ou clique no botão abaixo para selecionar';
    if (dropFormats) dropFormats.textContent = 'JPG, PNG, WEBP - max. 10MB';
  } else {
    const isVideo = identifyMode === 'video';
    const readable = isVideo ? 'vídeo' : 'áudio ou vídeo';
    if (selectLabel) selectLabel.textContent = isVideo ? 'Selecionar vídeo' : 'Selecionar áudio ou vídeo';
    if (dropTitle) dropTitle.textContent = `Arraste seu ${readable} aqui`;
    if (dropSubtitle) dropSubtitle.textContent = 'ou clique no botão abaixo para selecionar';
    if (dropFormats) dropFormats.textContent = isVideo ? 'MP4, MOV, AVI, MKV ou WEBM - max. 60s' : 'MP3, WAV, M4A, OGG, WEBM, MP4 ou MOV';
  }

  scrollToTopEaseOut(500);
}

/* ══════════════════════════════
   DOMContentLoaded
   ══════════════════════════════ */
/* BioVision Air */
function hideAirResult() {
  const result = document.getElementById('airResultado');
  if (!result) return;

  result.classList.remove('air-result-ready');
  result.hidden = true;
  result.innerHTML = '';
}

function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function showAirMessage(message, isError = false) {
  const result = document.getElementById('airResultado');
  if (!result) return;

  if (!isError) {
    hideAirResult();
    return;
  }

  result.hidden = false;
  result.classList.remove('air-result-ready');
  result.innerHTML = `<div class="air-error">${escapeHtml(message)}</div>`;
}

function formatFileSize(bytes) {
  if (!bytes) return '0 MB';
  const mb = bytes / (1024 * 1024);
  return `${mb.toFixed(mb >= 10 ? 0 : 1)} MB`;
}

function readVideoDuration(file) {
  return new Promise((resolve, reject) => {
    const video = document.createElement('video');
    const url = URL.createObjectURL(file);
    const cleanup = () => URL.revokeObjectURL(url);

    video.preload = 'metadata';
    video.onloadedmetadata = () => {
      const duration = Number(video.duration || 0);
      cleanup();
      if (!duration || !Number.isFinite(duration)) {
        reject(new Error('Não foi possível ler a duração do vídeo.'));
        return;
      }
      resolve(duration);
    };
    video.onerror = () => {
      cleanup();
      reject(new Error('Não foi possível abrir o vídeo selecionado.'));
    };
    video.src = url;
  });
}

async function handleAirVideo(file) {
  const main = document.querySelector('[data-max-duration]');
  const maxDuration = Number(main?.dataset.maxDuration || 30);
  const submit = document.getElementById('airSubmit');
  const previewWrap = document.getElementById('airVideoPreview');
  const preview = document.getElementById('airPreview');
  const fileInfo = document.getElementById('airFileInfo');

  if (!file || !file.type.startsWith('video/')) {
    airSelectedFile = null;
    if (submit) submit.disabled = true;
    showAirMessage('Selecione um arquivo de vídeo.', true);
    return;
  }

  try {
    const duration = await readVideoDuration(file);
    if (duration > maxDuration) {
      airSelectedFile = null;
      if (submit) submit.disabled = true;
      if (previewWrap) previewWrap.hidden = true;
      showAirMessage(`O vídeo tem ${duration.toFixed(2)} segundos e ultrapassa o limite de ${maxDuration} segundos.`, true);
      return;
    }

    airSelectedFile = file;
    if (submit) submit.disabled = false;
    if (preview && previewWrap) {
      preview.src = URL.createObjectURL(file);
      previewWrap.hidden = false;
    }
    if (fileInfo) {
      fileInfo.textContent = `${file.name} - ${duration.toFixed(2)}s - ${formatFileSize(file.size)}`;
    }
    hideAirResult();
  } catch (err) {
    airSelectedFile = null;
    if (submit) submit.disabled = true;
    showAirMessage(err.message || 'Não foi possível validar o vídeo.', true);
  }
}

function airTaxonDetailHtml(item, databaseAvailable) {
  const taxon = item.dados_taxon || null;
  const confidence = Number(item.confianca);

  if (!taxon) {
    const message = databaseAvailable
      ? item.classe === 'ave'
        ? 'Ave é uma categoria ampla. A ficha biológica exige a identificação de uma espécie específica.'
        : 'Esta categoria foi confirmada no vídeo, mas ainda não possui uma ficha correspondente no banco.'
      : 'A categoria foi confirmada, mas o banco de dados não respondeu durante esta análise.';
    return `
      <div class="air-taxon-empty">
        <span>Categoria confirmada</span>
        <h3>${escapeHtml(item.nome || item.classe || 'Animal')}</h3>
        <p>${escapeHtml(message)}</p>
      </div>
    `;
  }

  const scientificName = taxon.especie || item.nome || item.classe || 'Animal identificado';
  const popularNames = hasContent(taxon.nomes) ? taxon.nomes : item.nome;
  const mainImageTitle = hasContent(taxon.title_imagem) ? taxon.title_imagem : (popularNames || scientificName);
  const image = hasImage(taxon.imagem)
    ? `<img src="${imgSrc(taxon.imagem)}" alt="${escapeHtml(mainImageTitle)}" title="${escapeHtml(mainImageTitle)}">`
    : `<div class="air-taxon-image-empty" aria-hidden="true">${escapeHtml(String(item.nome || 'A').charAt(0))}</div>`;

  const taxonomyFields = [
    ['Reino', taxon.reino],
    ['Filo', taxon.filo],
    ['Classe', taxon.classe],
    ['Ordem', taxon.ordem],
    ['Família', taxon.familia],
    ['Gênero', taxon.genero]
  ].filter(([, value]) => hasContent(value));
  const taxonomy = taxonomyFields.length ? `
    <dl class="air-taxonomy-grid">
      ${taxonomyFields.map(([label, value]) => `
        <div><dt>${label}</dt><dd>${escapeHtml(value)}</dd></div>
      `).join('')}
    </dl>
  ` : '';

  const sections = [
    ['Habitat', taxon.habitat, null],
    ['Morfologia', taxon.morfologia, taxon.im_morfologia],
    ['Reprodução', taxon.reproducao, null],
    ['Conservação', taxon.conservacao, taxon.im_conservacao]
  ].filter(([, text, sectionImage]) => hasContent(text) || hasImage(sectionImage));
  const biologicalSections = sections.length ? `
    <div class="air-biology-sections">
      ${sections.map(([title, text, sectionImage], index) => `
        <details ${index === 0 ? 'open' : ''}>
          <summary>${title}</summary>
          ${hasContent(text) ? `<p>${escapeHtml(text)}</p>` : ''}
          ${hasImage(sectionImage) ? `<img src="${imgSrc(sectionImage)}" alt="${title}">` : ''}
        </details>
      `).join('')}
    </div>
  ` : '';

  return `
    <div class="air-taxon-header">
      <div class="air-taxon-image">${image}</div>
      <div>
        <span>Ficha biológica</span>
        <h3><i>${escapeHtml(scientificName)}</i></h3>
        ${hasContent(popularNames) ? `<p>${escapeHtml(popularNames)}</p>` : ''}
        ${Number.isFinite(confidence) ? `<small>${confidence.toFixed(1)}% de confiança na detecção</small>` : ''}
      </div>
    </div>
    ${taxonomy}
    ${biologicalSections}
  `;
}

function renderAirResult(data) {
  const result = document.getElementById('airResultado');
  if (!result) return;

  const labels = {
    boi: 'Boi',
    humano: 'Homo sapiens',
    cachorro: 'Cachorro',
    gato: 'Gato',
    ave: 'Ave'
  };
  const counts = data.contagens || {};
  const confidenceByClass = data.confianca_por_classe || {};
  const confirmedFrames = data.quadros_confirmados || {};
  const optionalNumber = value => value === null || value === undefined || value === ''
    ? Number.NaN
    : Number(value);
  const payloadEntries = Array.isArray(data.identificados) ? data.identificados : [];
  const entries = (payloadEntries.length ? payloadEntries : ['boi', 'humano', 'cachorro', 'gato', 'ave'].map(key => ({
    classe: key,
    nome: labels[key],
    quantidade: Number(counts[key] || 0),
    confianca: confidenceByClass[key],
    quadros_confirmados: confirmedFrames[key],
    dados_taxon: null
  })))
    .map(item => ({
      ...item,
      nome: item.nome || labels[item.classe] || item.classe,
      quantidade: Number(item.quantidade ?? counts[item.classe] ?? 0),
      confianca: optionalNumber(item.confianca ?? confidenceByClass[item.classe]),
      quadros_confirmados: Number(item.quadros_confirmados ?? confirmedFrames[item.classe] ?? 0)
    }))
    .filter(item => item.quantidade > 0)
    .sort((a, b) => b.quantidade - a.quantidade);

  const total = Number(data.total_estimado ?? entries.reduce((sum, item) => sum + item.quantidade, 0));
  const duration = Number(data.duracao);
  const analyzedFrames = Number(data.frames_analisados || 0);
  const topConfidence = optionalNumber(data.maior_confianca);
  const detections = entries.length ? entries.map((item, index) => `
    <button type="button" role="tab" class="air-detection-card ${index === 0 ? 'is-active' : ''}" data-air-result-index="${index}" aria-selected="${index === 0 ? 'true' : 'false'}">
      <span class="air-detection-index">${String(index + 1).padStart(2, '0')}</span>
      <span class="air-detection-name">
        <strong>${escapeHtml(item.nome)}</strong>
        <small>${item.quadros_confirmados} ${item.quadros_confirmados === 1 ? 'quadro confirmado' : 'quadros confirmados'}</small>
      </span>
      <span class="air-detection-count">
        <strong>${item.quantidade}</strong>
        <small>${item.quantidade === 1 ? 'animal' : 'animais'}</small>
      </span>
      <span class="air-detection-confidence">${Number.isFinite(item.confianca) ? `${item.confianca.toFixed(1)}%` : 'N/A'}</span>
    </button>
  `).join('') : `
    <div class="air-detection-empty">
      <span aria-hidden="true">00</span>
      <div>
        <strong>Nenhuma detecção consistente</strong>
        <p>Ocorrências isoladas foram descartadas para evitar uma contagem incorreta.</p>
      </div>
    </div>
  `;

  const information = entries.length ? `
    <section class="air-information-section" aria-label="Ficha biológica da categoria selecionada">
      <p class="air-information-label">Ficha da categoria selecionada</p>
      <div id="airTaxonDetail" class="air-taxon-detail"></div>
    </section>
  ` : '';

  result.hidden = false;
  result.innerHTML = `
    <section class="air-result-overview">
      <div class="air-result-head">
        <p class="air-result-label">Análise concluída</p>
        <h2><strong>${total}</strong><span>${total === 1 ? 'animal confirmado' : 'animais confirmados'}</span></h2>
        <p>A contagem considera somente ocorrências recorrentes ao longo do vídeo.</p>
      </div>
      <dl class="air-result-metrics">
        <div><dt>Duração</dt><dd>${Number.isFinite(duration) ? `${duration.toFixed(1)}s` : 'N/A'}</dd></div>
        <div><dt>Quadros</dt><dd>${analyzedFrames || 'N/A'}</dd></div>
        <div><dt>Maior confiança</dt><dd>${Number.isFinite(topConfidence) ? `${topConfidence.toFixed(1)}%` : 'N/A'}</dd></div>
      </dl>
    </section>
    <section class="air-result-catalog" aria-label="Animais identificados">
      <header>
        <span>Identificados</span>
        ${entries.length > 1 ? '<p>Selecione uma categoria para consultar sua ficha.</p>' : ''}
      </header>
      <div class="air-detection-list" role="tablist">${detections}</div>
    </section>
    ${information}
  `;

  const detail = result.querySelector('#airTaxonDetail');
  const pickerButtons = result.querySelectorAll('[data-air-result-index]');
  const renderDetail = index => {
    const item = entries[index] || entries[0];
    if (!detail || !item) return;
    detail.innerHTML = airTaxonDetailHtml(item, data.banco_disponivel !== false);
    pickerButtons.forEach((button, buttonIndex) => {
      const active = buttonIndex === index;
      button.classList.toggle('is-active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  };
  pickerButtons.forEach((button, index) => button.addEventListener('click', () => renderDetail(index)));
  renderDetail(0);

  result.classList.add('air-result-ready');
  setTimeout(() => result.scrollIntoView({ behavior: 'smooth', block: 'start' }), 80);
}

function resetAirAnalysis() {
  airSelectedFile = null;
  resetAnalysisProgress('air');

  const input = document.getElementById('airVideo');
  const preview = document.getElementById('airPreview');
  const previewWrap = document.getElementById('airVideoPreview');
  const submit = document.getElementById('airSubmit');
  const reset = document.getElementById('airReset');
  const fileInfo = document.getElementById('airFileInfo');
  const loading = document.getElementById('airLoading');

  if (input) input.value = '';
  if (preview) {
    if (preview.src) URL.revokeObjectURL(preview.src);
    preview.removeAttribute('src');
    preview.load();
  }
  if (previewWrap) previewWrap.hidden = true;
  if (submit) submit.disabled = true;
  if (reset) reset.hidden = true;
  if (loading) loading.hidden = true;
  if (fileInfo) fileInfo.textContent = '';

  const result = document.getElementById('airResultado');
  if (result) hideAirResult();
}

function closeChoiceMenus(except = null) {
  document.querySelectorAll('.choice-select.is-open').forEach(select => {
    if (select === except) return;
    select.classList.remove('is-open');
    const trigger = select.querySelector('.choice-trigger');
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
  });
}

function setIdentifyMode(mode, label) {
  identifyMode = mode || 'photo';

  const input = document.getElementById('imagem');
  const selectLabel = document.getElementById('imageSelectLabel');
  const dropTitle = document.querySelector('#uploadDropzone .upload-title');
  const dropSubtitle = document.querySelector('#uploadDropzone .upload-subtitle');
  const dropFormats = document.querySelector('#uploadDropzone .upload-formats');
  const result = document.getElementById('resultado');

  resetIdentification();

  if (identifyMode === 'photo') {
    if (input) input.accept = 'image/*';
    if (selectLabel) selectLabel.textContent = 'Selecionar imagem';
    if (dropTitle) dropTitle.textContent = 'Arraste sua imagem aqui';
    if (dropSubtitle) dropSubtitle.textContent = 'ou clique no botão abaixo para selecionar';
    if (dropFormats) dropFormats.textContent = 'JPG, PNG, WEBP - max. 10MB';
    return;
  }

  const isVideo = identifyMode === 'video';
  const readable = isVideo ? 'vídeo' : 'áudio ou vídeo';
  if (input) input.accept = isVideo ? 'video/*' : 'audio/*,video/*';
  if (selectLabel) selectLabel.textContent = isVideo ? 'Selecionar vídeo' : 'Selecionar áudio ou vídeo';
  if (dropTitle) dropTitle.textContent = `Arraste seu ${readable} aqui`;
  if (dropSubtitle) dropSubtitle.textContent = 'ou clique no botão abaixo para selecionar';
  if (dropFormats) dropFormats.textContent = isVideo ? 'MP4, MOV, AVI, MKV ou WEBM - max. 60s' : 'MP3, WAV, M4A, OGG, WEBM, MP4 ou MOV';
  if (result) result.innerHTML = '';
}

function initChoiceSelects() {
  const selects = document.querySelectorAll('[data-choice-select]');
  if (!selects.length) return;

  selects.forEach(select => {
    const trigger = select.querySelector('.choice-trigger');
    const labelEl = trigger?.querySelector('span:first-child');
    const options = select.querySelectorAll('.choice-option');
    const group = select.dataset.choiceGroup;

    if (!trigger || !options.length) return;

    trigger.addEventListener('click', (event) => {
      event.stopPropagation();
      const willOpen = !select.classList.contains('is-open');
      closeChoiceMenus(select);
      select.classList.toggle('is-open', willOpen);
      trigger.setAttribute('aria-expanded', willOpen ? 'true' : 'false');
    });

    options.forEach(option => {
      option.addEventListener('click', () => {
        const value = option.dataset.choiceValue;
        const label = option.dataset.choiceLabel || option.textContent.trim();
        options.forEach(item => item.classList.toggle('is-selected', item === option));
        if (labelEl) labelEl.textContent = label;
        closeChoiceMenus();

        if (group === 'identify') {
          const hidden = document.getElementById('identifyModeValue');
          if (hidden) hidden.value = value;
          setIdentifyMode(value, label);
        }

        if (group === 'air-target') {
          const hidden = document.getElementById('airTargetValue');
          if (hidden) hidden.value = value;
        }

      });
    });
  });

  document.addEventListener('click', () => closeChoiceMenus());
}

function initIdentifyModes() {
  const hidden = document.getElementById('identifyModeValue');
  if (hidden) setIdentifyMode(hidden.value || 'photo', 'Imagem');
}

function initBioVisionAir() {
  const form = document.getElementById('airForm');
  if (!form) return;

  const input = document.getElementById('airVideo');
  const selectBtn = document.getElementById('airSelectBtn');
  const dropzone = document.getElementById('airDropzone');
  const loading = document.getElementById('airLoading');
  const submit = document.getElementById('airSubmit');
  const reset = document.getElementById('airReset');

  const openPicker = () => input?.click();
  if (selectBtn) selectBtn.addEventListener('click', openPicker);
  if (dropzone) {
    dropzone.addEventListener('click', openPicker);
    dropzone.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openPicker();
      }
    });

    ['dragenter', 'dragover'].forEach(type => {
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.add('drag-over');
      });
    });
    ['dragleave', 'drop'].forEach(type => {
      dropzone.addEventListener(type, (event) => {
        event.preventDefault();
        dropzone.classList.remove('drag-over');
      });
    });
    dropzone.addEventListener('drop', (event) => {
      const file = event.dataTransfer.files[0];
      if (file && input) {
        const transfer = new DataTransfer();
        transfer.items.add(file);
        input.files = transfer.files;
        handleAirVideo(file);
      }
    });
  }

  if (input) {
    input.addEventListener('change', (event) => handleAirVideo(event.target.files[0]));
  }

  if (form) {
    form.addEventListener('submit', async (event) => {
      event.preventDefault();
      if (!airSelectedFile) {
        showAirMessage('Selecione um vídeo antes de processar.', true);
        return;
      }

      const formData = new FormData();
      formData.append('video', airSelectedFile, airSelectedFile.name);
      formData.append(
        'target_class',
        document.getElementById('airTargetValue')?.value || 'boi'
      );

      try {
        resetAnalysisProgress('air');
        if (loading) {
          loading.hidden = false;
          setTimeout(() => loading.scrollIntoView({ behavior: 'smooth', block: 'center' }), 80);
        }
        if (submit) submit.disabled = true;
        showAirMessage('Processando vídeo. Isso pode levar alguns instantes.');

        const data = await runAnalysisJob(
          '/biovision/contar-animais',
          formData,
          state => updateAnalysisProgress('air', state)
        );

        if (data.erro) {
          showAirMessage(data.erro || 'Falha ao processar o vídeo.', true);
          return;
        }

        renderAirResult(data);
        if (reset) reset.hidden = false;
      } catch (err) {
        showAirMessage(err.message || 'Falha ao comunicar com o servidor.', true);
      } finally {
        if (loading) loading.hidden = true;
        if (submit) submit.disabled = !airSelectedFile;
      }
    });
  }

  if (reset) {
    reset.addEventListener('click', resetAirAnalysis);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const zoomModal = document.getElementById("zoomModal");
  const zoomContent = document.getElementById("zoomContent");
  const inputFile = document.getElementById("imagem");
  const btnStart = document.getElementById("btnIdentificar");
  const btnNova = document.getElementById("btnNova");

  initChoiceSelects();
  initIdentifyModes();
  initBioVisionAir();
  initFloatingHeader();
  initLandingHero();

  if (inputFile) {
    inputFile.addEventListener("change", (e) => mostrarImagem(e.target));
  }

  if (btnStart) {
    btnStart.addEventListener("click", () => {
      if (!lastSelectedFile) {
        alert("Selecione um arquivo primeiro.");
        return;
      }
      enviarImagemArquivo(lastSelectedFile);
    });
  }

  if (btnNova) {
    btnNova.addEventListener("click", resetIdentification);
  }

  // ── Zoom modal (long press) ──
  if (zoomModal && zoomContent) {
    const openZoom = (card) => {
      zoomContent.innerHTML = card.innerHTML;
      zoomModal.style.display = "flex";
      document.body.style.overflow = "hidden";
    };

    const closeZoom = () => {
      zoomModal.style.display = "none";
      zoomContent.innerHTML = "";
      document.body.style.overflow = "auto";
    };

    let pressTimer = null;
    let targetCard = null;

    const startPress = (e) => {
      const card = e.target.closest(".card");
      if (!card) return;
      targetCard = card;
      pressTimer = setTimeout(() => openZoom(targetCard), 150);
    };

    const cancelPress = () => {
      clearTimeout(pressTimer);
      pressTimer = null;
      targetCard = null;
    };

    document.addEventListener("touchstart", startPress);
    document.addEventListener("touchend", cancelPress);
    document.addEventListener("touchmove", cancelPress);
    document.addEventListener("mousedown", startPress);
    document.addEventListener("mouseup", cancelPress);
    document.addEventListener("mouseleave", cancelPress);

    zoomModal.addEventListener("click", (e) => {
      if (e.target === zoomModal) closeZoom();
    });
  }

  // ── Init features ──
  initScrollReveal();
  initDragDrop();
  initAccordion();
  initNumberCounters();
  initTiltEffect();

  // ── Load counter on any page that has it ──
  const counterEl = document.getElementById('statCounter');
  if (counterEl) counterEl.textContent = getCount();
});

/* ══════════════════════════════
   ACCORDION
   ══════════════════════════════ */
function initAccordion() {
  const items = document.querySelectorAll('.acc-item');
  if (!items.length) return;

  items.forEach(item => {
    const trigger = item.querySelector('.acc-trigger');
    if (!trigger) return;

    trigger.addEventListener('click', () => {
      const isOpen = item.classList.contains('is-open');

      // Close all
      items.forEach(i => {
        i.classList.remove('is-open');
        const t = i.querySelector('.acc-trigger');
        if (t) t.setAttribute('aria-expanded', 'false');
      });

      // Open clicked (if it was closed)
      if (!isOpen) {
        item.classList.add('is-open');
        trigger.setAttribute('aria-expanded', 'true');
      }
    });
  });
}

/* ══════════════════════════════
   NUMBER COUNTER ON SCROLL
   ══════════════════════════════ */
function initNumberCounters() {
  const cards = document.querySelectorAll('.number-value[data-count]');
  if (!cards.length) return;

  let animated = false;

  const observer = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (entry.isIntersecting && !animated) {
        animated = true;
        cards.forEach(el => {
          const target = parseInt(el.dataset.count, 10);
          const duration = 1200;
          const t0 = performance.now();
          const easeOut = t => 1 - Math.pow(1 - t, 4);

          function tick(now) {
            const p = Math.min(1, (now - t0) / duration);
            el.textContent = Math.round(target * easeOut(p));
            if (p < 1) requestAnimationFrame(tick);
          }
          requestAnimationFrame(tick);
        });
      }
    });
  }, { threshold: 0.3 });

  const section = document.querySelector('.numbers-section');
  if (section) observer.observe(section);
}

/* ══════════════════════════════
   TILT EFFECT ON CARDS
   ══════════════════════════════ */
function initTiltEffect() {
  return;
  const cards = document.querySelectorAll('[data-tilt]');
  if (!cards.length) return;
  if (window.matchMedia('(hover: none)').matches) return; // skip touch devices

  cards.forEach(card => {
    card.addEventListener('mousemove', (e) => {
      const rect = card.getBoundingClientRect();
      const x = (e.clientX - rect.left) / rect.width - 0.5;
      const y = (e.clientY - rect.top) / rect.height - 0.5;
      card.style.transform = `perspective(600px) rotateY(${x * 8}deg) rotateX(${-y * 8}deg) translateY(-4px)`;
    });

    card.addEventListener('mouseleave', () => {
      card.style.transform = '';
    });
  });
}
