const state = {
  currentQuestion: null,
  testQuestions: [],
  currentIndex: 0,
  results: [],
  checkedCurrent: false,
};

const $ = (selector) => document.querySelector(selector);

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function cleanKeywords(chapter) {
  if (chapter.title.startsWith("1. Множества")) {
    return [
      "Множества",
      "Отношения",
      "Алгебра подмножеств",
      "Представление множеств",
      "Замыкание и сокращение отношений",
      "Функции",
      "Отношения эквивалентности",
      "Отношения порядка",
      "Характеристические функции",
    ];
  }
  return chapter.keywords.filter((keyword) => keyword !== "программах");
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
    ...cleanKeywords(meta.selected_chapter).map((keyword) => el("span", "tag", keyword))
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

function renderTrainerQuestion() {
  const question = state.testQuestions[state.currentIndex];
  state.currentQuestion = question;
  state.checkedCurrent = false;
  $("#trainerQuestion").textContent = question.question || "Вопросы не найдены";
  $("#trainerSource").textContent = question.section
    ? `${question.section} · стр. ${question.page}`
    : "";
  $("#trainerProgress").textContent = state.testQuestions.length
    ? `Вопрос ${state.currentIndex + 1} из ${state.testQuestions.length}`
    : "Тест";
  $("#trainerAnswer").value = "";
  $("#trainerResult").className = "result empty";
  $("#trainerResult").textContent = "";
  $("#checkAnswer").disabled = false;
  $("#nextQuestion").disabled = true;
  $("#nextQuestion").textContent =
    state.currentIndex + 1 >= state.testQuestions.length ? "Итог" : "Следующий";
}

async function loadTrainerTest() {
  const test = await api("/api/trainer/test?count=5");
  state.testQuestions = test.questions || [];
  state.currentIndex = 0;
  state.results = [];
  if (state.testQuestions.length) {
    renderTrainerQuestion();
    return;
  }
  state.currentQuestion = null;
  $("#trainerProgress").textContent = "Тест";
  $("#trainerQuestion").textContent = "Вопросы не найдены";
  $("#trainerSource").textContent = "";
  $("#trainerAnswer").value = "";
  $("#trainerResult").className = "result empty";
  $("#trainerResult").textContent = "";
  $("#checkAnswer").disabled = true;
  $("#nextQuestion").disabled = true;
}

async function checkTrainerAnswer(event) {
  event.preventDefault();
  if (!state.currentQuestion || state.checkedCurrent) return;
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
    state.results[state.currentIndex] = result;
    state.checkedCurrent = true;
    $("#nextQuestion").disabled = false;
  } catch (error) {
    const box = $("#trainerResult");
    box.className = "result wrong";
    box.textContent = "Не удалось проверить ответ.";
    $("#checkAnswer").disabled = false;
  }
}

function showTrainerSummary() {
  const correct = state.results.filter((result) => result && result.correct).length;
  const total = state.testQuestions.length;
  state.currentQuestion = null;
  state.checkedCurrent = true;
  $("#trainerProgress").textContent = "Итог";
  $("#trainerQuestion").textContent = `Результат: ${correct} из ${total}`;
  $("#trainerSource").textContent = "";
  $("#trainerAnswer").value = "";
  $("#checkAnswer").disabled = true;
  $("#nextQuestion").disabled = true;
  $("#trainerResult").className = correct === total ? "result correct" : "result wrong";
  $("#trainerResult").textContent =
    correct === total
      ? "Все ответы зачтены."
      : "Можно запустить новый тест и пройти вопросы еще раз.";
}

function nextTrainerQuestion() {
  if (!state.checkedCurrent) return;
  if (state.currentIndex + 1 >= state.testQuestions.length) {
    showTrainerSummary();
    return;
  }
  state.currentIndex += 1;
  renderTrainerQuestion();
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
  $("#nextQuestion").addEventListener("click", nextTrainerQuestion);
  $("#newTest").addEventListener("click", loadTrainerTest);
  try {
    renderMeta(await api("/api/meta"));
    await loadTrainerTest();
  } catch (error) {
    addMessage("system", "Индекс не загружен. Проверьте data/index.json.");
  }
}

init();
