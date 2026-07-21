const $ = (selector) => document.querySelector(selector);
let job = "";

const statusLabels = {
  GENERATED: "Croqui gerado e validado",
  NEEDS_REVIEW: "Motor bloqueou a geração: revisão necessária",
  READY_TO_GENERATE: "Plano técnico validado",
};

function setFacts(data) {
  const facts = [
    ["Município", data.municipio],
    ["Obra", data.obra],
    ["Data", data.data_projeto],
    ["Ações", (data.acoes || []).map((item) => `${item.acao} ${item.tipo} ${item.numero}`).join(", ")],
    ["Identificadores", (data.identificadores || []).join(", ")],
    ["Páginas", data.pages],
  ];
  const root = $("#facts");
  root.replaceChildren();
  root.className = "facts";
  for (const [label, value] of facts) {
    const card = document.createElement("div");
    card.className = "fact";
    const title = document.createElement("b");
    title.textContent = label;
    card.append(title, document.createTextNode(value || "—"));
    root.append(card);
  }
}

function setStatus(data) {
  const root = $("#engine-status");
  root.replaceChildren();
  const title = document.createElement("strong");
  title.textContent = statusLabels[data.status] || data.status;
  root.append(title);
  const issues = (data.validation && data.validation.issues) || [];
  if (issues.length) {
    const list = document.createElement("ul");
    for (const issue of issues) {
      const item = document.createElement("li");
      item.textContent = issue.message;
      list.append(item);
    }
    root.append(list);
  }
  root.className = data.status === "GENERATED" ? "engine-status ok" : "engine-status";
}

function setDownloads(data) {
  const root = $("#downloads");
  root.replaceChildren();
  const available = data.artifacts || {};
  const kinds = [
    ["pdf", "Baixar PDF"],
    ["xls", "Baixar Excel .xls"],
    ["xlsx", "Baixar Excel .xlsx"],
    ["report", "Baixar relatório"],
  ];
  for (const [kind, label] of kinds) {
    if (!available[kind]) continue;
    const link = document.createElement("a");
    link.className = "download";
    link.href = `/api/jobs/${job}/download/${kind}`;
    link.textContent = label;
    root.append(link);
  }
  root.className = root.children.length ? "downloads" : "downloads hidden";
}

async function responseError(response) {
  try {
    const data = await response.json();
    return data.detail || "Não foi possível concluir o processamento";
  } catch {
    return "Não foi possível concluir o processamento";
  }
}

fetch("/api/health")
  .then((response) => response.json())
  .then(() => {
    $("#mode").textContent = "Processamento técnico";
  });

$("#upload").onsubmit = async (event) => {
  event.preventDefault();
  const button = event.submitter;
  const startedAt = Date.now();
  const updateElapsed = () => {
    const elapsed = Math.floor((Date.now() - startedAt) / 1000);
    const minutes = Math.floor(elapsed / 60);
    const seconds = String(elapsed % 60).padStart(2, "0");
    button.textContent = `Analisando… ${minutes}:${seconds}`;
  };
  button.disabled = true;
  updateElapsed();
  const elapsedTimer = window.setInterval(updateElapsed, 1000);
  try {
    const response = await fetch("/api/analisar", { method: "POST", body: new FormData(event.target) });
    if (!response.ok) throw new Error(await responseError(response));
    const data = await response.json();
    job = data.job_id;
    setFacts(data);
    setStatus(data);
    setDownloads(data);
    $("#confirm [name=job_id]").value = job;
    if (data.tipo_isolamento) $("#confirm [name=tipo]").value = data.tipo_isolamento;
    if (data.equipamento_isolamento) $("#confirm [name=numero]").value = data.equipamento_isolamento;
    $("#result").classList.remove("hidden");
  } catch (error) {
    alert(error.message);
  } finally {
    window.clearInterval(elapsedTimer);
    button.disabled = false;
    button.textContent = "Analisar projeto";
  }
};

$("#confirm").onsubmit = async (event) => {
  event.preventDefault();
  const response = await fetch("/api/confirmar", { method: "POST", body: new FormData(event.target) });
  if (!response.ok) return alert(await responseError(response));
  const data = await response.json();
  setStatus(data);
  setDownloads(data);
};
