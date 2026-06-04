const state = {
  currentQuestion: null,
};

const $ = (selector) => document.querySelector(selector);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json();
}

function renderMeta(meta) {
  $("#sourceLabel").textContent = meta.source_pdf;
  $("#chapterCount").textContent = meta.chapter_count;
  $("#pageCount").textContent = meta.page_count;
  $("#chapterTitle").textContent = meta.selected_chapter.title;
  $("#chapterPages").textContent = `PDF-страницы ${meta.selected_chapter.page_start}-${meta.selected_chapter.page_end}`;

  const keywords = $("#keywords");
  keywords.replaceChildren(
    ...meta.selected_chapter.keywords.map((keyword) => el("span", "tag", keyword))
  );

  const chapters = $("#chapters");
  chapters.replaceChildren(
    ...meta.chapters.map((chapter) => {
      const item = el("li");
      item.textContent = `${chapter.title} · стр. ${chapter.page_start}`;
      return item;
    })
  );
}

function addMessage(kind, text, citations = []) {
  const article = el("article", `message ${kind}`);
  const paragraph = el("p", null, text);
  article.append(paragraph);
  if (citations.length) {
    const sources = el("div", "sources");
    for (const citation of citations) {
      const pages =
        citation.page_start === citation.page_end
          ? `стр. ${citation.page_start}`
          : `стр. ${citation.page_start}-${citation.page_end}`;
      sources.append(el("span", "source", `${citation.section} · ${pages}`));
    }
    article.append(sources);
  }
  $("#messages").append(article);
  article.scrollIntoView({ block: "end", behavior: "smooth" });
}

async function askQuestion(event) {
  event.preventDefault();
  const input = $("#questionInput");
  const question = input.value.trim();
  if (!question) return;

  addMessage("user", question);
  input.value = "";
  $("#askButton").disabled = true;
  try {
    const result = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question }),
    });
    addMessage("system", result.answer, result.citations || []);
  } catch (error) {
    addMessage("system", "Не удалось получить ответ от локального сервера.");
  } finally {
    $("#askButton").disabled = false;
    input.focus();
  }
}

async function loadTrainerQuestion() {
  const question = await api("/api/trainer/question");
  state.currentQuestion = question;
  $("#trainerQuestion").textContent = question.question || "Вопросы не найдены";
  $("#trainerSource").textContent = question.section
    ? `${question.section} · стр. ${question.page}`
    : "";
  $("#trainerAnswer").value = "";
  $("#trainerResult").className = "result empty";
  $("#trainerResult").textContent = "";
}

async function checkTrainerAnswer(event) {
  event.preventDefault();
  if (!state.currentQuestion) return;
  const answer = $("#trainerAnswer").value.trim();
  if (!answer) return;

  $("#checkAnswer").disabled = true;
  try {
    const result = await api("/api/trainer/check", {
      method: "POST",
      body: JSON.stringify({ id: state.currentQuestion.id, answer }),
    });
    const box = $("#trainerResult");
    box.className = `result ${result.correct ? "correct" : "wrong"}`;
    const verdict = result.correct ? "Засчитано" : "Не засчитано";
    box.textContent = `${verdict}. ${result.explanation}`;
  } catch (error) {
    const box = $("#trainerResult");
    box.className = "result wrong";
    box.textContent = "Не удалось проверить ответ.";
  } finally {
    $("#checkAnswer").disabled = false;
  }
}

function setupTabs() {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((item) => item.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((item) => item.classList.remove("active"));
      tab.classList.add("active");
      $(`#${tab.dataset.view}View`).classList.add("active");
    });
  }
}

async function init() {
  setupTabs();
  $("#askForm").addEventListener("submit", askQuestion);
  $("#trainerForm").addEventListener("submit", checkTrainerAnswer);
  $("#nextQuestion").addEventListener("click", loadTrainerQuestion);
  try {
    renderMeta(await api("/api/meta"));
    await loadTrainerQuestion();
  } catch (error) {
    addMessage("system", "Индекс не загружен. Проверьте data/index.json.");
  }
}

init();
