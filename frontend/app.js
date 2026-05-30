/*
  margin — frontend app
  =====================
  multi-view SPA: dashboard (courses) → course (assignments) → assignment detail.
  no framework, just firebase auth + vanilla DOM.
*/

import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

// ---------------------------------------------------------------------------
// firebase config
// ---------------------------------------------------------------------------
const firebaseConfig = {
  apiKey: "AIzaSyC59bJU9rhAezTFyfOSi138G9xgxXXM8oQ",
  authDomain: "margin-app-4c293.firebaseapp.com",
  projectId: "margin-app-4c293",
  storageBucket: "margin-app-4c293.firebasestorage.app",
  messagingSenderId: "358817583449",
  appId: "1:358817583449:web:7b392f1b84693cd3717c48",
};

const fbApp = initializeApp(firebaseConfig);
const auth = getAuth(fbApp);
const provider = new GoogleAuthProvider();

const API_BASE = "http://localhost:5000/api";


// ---------------------------------------------------------------------------
// API helper
// ---------------------------------------------------------------------------
async function api(path, options = {}) {
  const user = auth.currentUser;
  if (!user) throw new Error("not signed in");

  const idToken = await user.getIdToken();

  // pdf upload goes as FormData, everything else as JSON
  const isFormData = options.body instanceof FormData;

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      Authorization: `Bearer ${idToken}`,
      ...(isFormData ? {} : { "Content-Type": "application/json" }),
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    const err = await response.json().catch(() => ({ error: `request failed: ${response.status}` }));
    throw new Error(err.error || `request failed: ${response.status}`);
  }

  return response.json();
}


// ---------------------------------------------------------------------------
// app state
// ---------------------------------------------------------------------------
let state = {
  currentCourse: null,   // { id, name, color }
  currentProject: null,  // { id, title, dueDate, extractedText, totalEstimatedMinutes }
  tasks: [],             // tasks for currentProject
};


// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const signedOutView   = document.getElementById("signed-out-view");
const signedInView    = document.getElementById("signed-in-view");
const userEmailEl     = document.getElementById("user-email");
const signInBtn       = document.getElementById("sign-in-btn");
const signOutBtn      = document.getElementById("sign-out-btn");
const breadcrumb      = document.getElementById("breadcrumb");
const scheduleBtn     = document.getElementById("schedule-btn");

// views
const viewDashboard   = document.getElementById("view-dashboard");
const viewCourse      = document.getElementById("view-course");
const viewAssignment  = document.getElementById("view-assignment");

// dashboard
const coursesList     = document.getElementById("courses-list");
const coursesEmpty    = document.getElementById("courses-empty");
const addCourseBtn    = document.getElementById("add-course-btn");

// course view
const courseTitle     = document.getElementById("course-title");
const assignmentsList = document.getElementById("assignments-list");
const assignmentsEmpty = document.getElementById("assignments-empty");
const addAssignmentBtn = document.getElementById("add-assignment-btn");

// assignment view
const assignmentTitleEl  = document.getElementById("assignment-title");
const assignmentDueEl    = document.getElementById("assignment-due");
const estimateBanner     = document.getElementById("estimate-banner");
const estimateText       = document.getElementById("estimate-text");
const progressFill       = document.getElementById("progress-fill");
const uploadPdfBtn       = document.getElementById("upload-pdf-btn");
const uploadPromptBtn    = document.getElementById("upload-prompt-btn");
const autoScheduleBtn    = document.getElementById("auto-schedule-btn");
const pdfFileInput       = document.getElementById("pdf-file-input");
const pdfSpinner         = document.getElementById("pdf-spinner");
const textPanel          = document.getElementById("assignment-text-panel");
const textEmpty          = document.getElementById("assignment-text-empty");
const textContent        = document.getElementById("assignment-text-content");
const taskList           = document.getElementById("task-list");
const tasksEmpty         = document.getElementById("tasks-empty");
const addTaskBtn         = document.getElementById("add-task-btn");

// modals
const courseModal        = document.getElementById("course-modal");
const courseNameInput    = document.getElementById("course-name-input");
const courseColorInput   = document.getElementById("course-color-input");
const cancelCourseBtn    = document.getElementById("cancel-course-btn");
const saveCourseBtn      = document.getElementById("save-course-btn");

const assignmentModal    = document.getElementById("assignment-modal");
const assignmentTitleInput = document.getElementById("assignment-title-input");
const assignmentDueInput = document.getElementById("assignment-due-input");
const cancelAssignmentBtn = document.getElementById("cancel-assignment-btn");
const saveAssignmentBtn  = document.getElementById("save-assignment-btn");

const taskModal          = document.getElementById("task-modal");
const taskTitleInput     = document.getElementById("task-title-input");
const taskMinutesInput   = document.getElementById("task-minutes-input");
const cancelTaskBtn      = document.getElementById("cancel-task-btn");
const saveTaskBtn        = document.getElementById("save-task-btn");

const scheduleModal      = document.getElementById("schedule-modal");
const scheduleBlocksList = document.getElementById("schedule-blocks-list");
const addBlockBtn        = document.getElementById("add-block-btn");
const cancelScheduleBtn  = document.getElementById("cancel-schedule-btn");
const saveScheduleBtn    = document.getElementById("save-schedule-btn");


// ---------------------------------------------------------------------------
// navigation
// ---------------------------------------------------------------------------
const VIEWS = { dashboard: viewDashboard, course: viewCourse, assignment: viewAssignment };

function showView(name) {
  for (const [key, el] of Object.entries(VIEWS)) {
    el.classList.toggle("hidden", key !== name);
  }
  renderBreadcrumb(name);
}

function renderBreadcrumb(view) {
  breadcrumb.innerHTML = "";

  const sep = () => {
    const s = document.createElement("span");
    s.className = "breadcrumb-sep";
    s.textContent = "›";
    return s;
  };

  if (view === "dashboard") return;

  const dashLink = document.createElement("span");
  dashLink.className = "breadcrumb-link";
  dashLink.textContent = "courses";
  dashLink.addEventListener("click", () => showView("dashboard"));
  breadcrumb.appendChild(sep());
  breadcrumb.appendChild(dashLink);

  if (view === "course" && state.currentCourse) {
    const cur = document.createElement("span");
    cur.className = "breadcrumb-current";
    cur.textContent = state.currentCourse.name;
    breadcrumb.appendChild(sep());
    breadcrumb.appendChild(cur);
  }

  if (view === "assignment" && state.currentCourse && state.currentProject) {
    const courseLink = document.createElement("span");
    courseLink.className = "breadcrumb-link";
    courseLink.textContent = state.currentCourse.name;
    courseLink.addEventListener("click", () => {
      showView("course");
      loadAssignments(state.currentCourse.id);
    });
    // replace the static dash link with a clickable course link
    breadcrumb.innerHTML = "";
    breadcrumb.appendChild(sep());
    const dl2 = document.createElement("span");
    dl2.className = "breadcrumb-link";
    dl2.textContent = "courses";
    dl2.addEventListener("click", () => showView("dashboard"));
    breadcrumb.appendChild(dl2);
    breadcrumb.appendChild(sep());
    breadcrumb.appendChild(courseLink);

    const cur = document.createElement("span");
    cur.className = "breadcrumb-current";
    cur.textContent = state.currentProject.title;
    breadcrumb.appendChild(sep());
    breadcrumb.appendChild(cur);
  }
}


// ---------------------------------------------------------------------------
// auth
// ---------------------------------------------------------------------------
signInBtn.addEventListener("click", async () => {
  try {
    await signInWithPopup(auth, provider);
  } catch (err) {
    console.error("sign-in failed:", err);
    alert(`sign-in failed: ${err.message}`);
  }
});

signOutBtn.addEventListener("click", () => signOut(auth));

onAuthStateChanged(auth, async (user) => {
  if (user) {
    signedOutView.classList.add("hidden");
    signedInView.classList.remove("hidden");
    userEmailEl.textContent = user.email;

    try {
      await api("/users/init", {
        method: "POST",
        body: JSON.stringify({ displayName: user.displayName || "" }),
      });
      showView("dashboard");
      await loadCourses();
    } catch (err) {
      console.error("init failed:", err);
      alert(`oops: ${err.message}`);
    }
  } else {
    signedInView.classList.add("hidden");
    signedOutView.classList.remove("hidden");
    userEmailEl.textContent = "";
  }
});


// ---------------------------------------------------------------------------
// courses
// ---------------------------------------------------------------------------
async function loadCourses() {
  try {
    const data = await api("/courses");
    renderCourses(data.courses);
  } catch (err) {
    console.error("failed to load courses:", err);
  }
}

function renderCourses(courses) {
  coursesList.innerHTML = "";

  if (courses.length === 0) {
    coursesList.appendChild(coursesEmpty);
    coursesEmpty.classList.remove("hidden");
    return;
  }

  coursesEmpty.classList.add("hidden");

  for (const course of courses) {
    const card = document.createElement("div");
    card.className = "course-card";
    card.style.setProperty("--course-color", course.color);

    const heading = document.createElement("h3");
    heading.textContent = course.name;
    card.appendChild(heading);

    card.addEventListener("click", () => openCourse(course));
    coursesList.appendChild(card);
  }
}

async function openCourse(course) {
  state.currentCourse = course;
  courseTitle.textContent = course.name;
  showView("course");
  await loadAssignments(course.id);
}

// add course modal
addCourseBtn.addEventListener("click", () => {
  courseNameInput.value = "";
  courseColorInput.value = "#a8d5ba";
  courseModal.classList.remove("hidden");
  courseNameInput.focus();
});

cancelCourseBtn.addEventListener("click", () => courseModal.classList.add("hidden"));

saveCourseBtn.addEventListener("click", async () => {
  const name = courseNameInput.value.trim();
  if (!name) { alert("give your course a name 💛"); return; }
  saveCourseBtn.disabled = true;
  try {
    await api("/courses", {
      method: "POST",
      body: JSON.stringify({ name, color: courseColorInput.value }),
    });
    courseModal.classList.add("hidden");
    await loadCourses();
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveCourseBtn.disabled = false;
  }
});

courseNameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveCourseBtn.click(); });


// ---------------------------------------------------------------------------
// assignments
// ---------------------------------------------------------------------------
async function loadAssignments(courseId) {
  try {
    const data = await api(`/projects?courseId=${courseId}`);
    renderAssignments(data.projects);
  } catch (err) {
    console.error("failed to load assignments:", err);
  }
}

function renderAssignments(projects) {
  assignmentsList.innerHTML = "";

  if (projects.length === 0) {
    assignmentsList.appendChild(assignmentsEmpty);
    assignmentsEmpty.classList.remove("hidden");
    return;
  }

  assignmentsEmpty.classList.add("hidden");

  for (const project of projects) {
    const card = document.createElement("div");
    card.className = "assignment-card";

    const colorBar = document.createElement("div");
    colorBar.className = "assignment-card-color";
    colorBar.style.setProperty("--course-color", state.currentCourse?.color || "#ccc");
    card.appendChild(colorBar);

    const body = document.createElement("div");
    body.className = "assignment-card-body";

    const title = document.createElement("div");
    title.className = "assignment-card-title";
    title.textContent = project.title;
    body.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "assignment-card-meta";
    if (project.dueDate) {
      const due = formatDate(project.dueDate);
      const span = document.createElement("span");
      span.textContent = `due ${due}`;
      meta.appendChild(span);
    }
    if (project.totalEstimatedMinutes) {
      const est = document.createElement("span");
      est.textContent = `~${formatMinutes(project.totalEstimatedMinutes)}`;
      meta.appendChild(est);
    }
    body.appendChild(meta);
    card.appendChild(body);

    card.addEventListener("click", () => openAssignment(project));
    assignmentsList.appendChild(card);
  }
}

async function openAssignment(project) {
  state.currentProject = project;
  state.tasks = [];
  showView("assignment");
  renderAssignmentHeader(project);

  // load tasks
  try {
    const data = await api(`/tasks?projectId=${project.id}`);
    state.tasks = data.tasks;
    renderAssignmentText(project);
    renderTasks(data.tasks);
    renderEstimateBanner(data.tasks, project);
  } catch (err) {
    console.error("failed to load tasks:", err);
  }
}

function renderAssignmentHeader(project) {
  assignmentTitleEl.textContent = project.title;
  assignmentDueEl.textContent = "";
  assignmentDueEl.className = "due-badge";

  if (project.dueDate) {
    const daysLeft = daysUntil(project.dueDate);
    assignmentDueEl.textContent = `due ${formatDate(project.dueDate)}`;
    if (daysLeft < 0) assignmentDueEl.classList.add("overdue");
    else if (daysLeft <= 2) assignmentDueEl.classList.add("soon");
  }
}

function renderAssignmentText(project) {
  if (project.extractedText) {
    textEmpty.classList.add("hidden");
    textContent.classList.remove("hidden");
    textContent.textContent = project.extractedText;
  } else {
    textContent.classList.add("hidden");
    textEmpty.classList.remove("hidden");
  }
}

function renderEstimateBanner(tasks, project) {
  if (!tasks.length) {
    estimateBanner.classList.add("hidden");
    return;
  }

  const total = tasks.reduce((s, t) => s + (t.estimatedMinutes || 0), 0);
  const done = tasks.filter(t => t.status === "done");
  const doneMinutes = done.reduce((s, t) => s + (t.estimatedMinutes || 0), 0);
  const remaining = total - doneMinutes;
  const pct = total > 0 ? Math.round((doneMinutes / total) * 100) : 0;

  const actualSpent = done.reduce((s, t) => s + (t.timeSpent || 0), 0);
  let estimateMsg = `~${formatMinutes(remaining)} remaining`;
  if (actualSpent > 0) {
    estimateMsg += ` · ${formatMinutes(actualSpent)} recorded so far`;
  } else if (total > 0) {
    estimateMsg = `estimated ${formatMinutes(total)} total · ${formatMinutes(remaining)} remaining`;
  }

  estimateText.textContent = estimateMsg;
  progressFill.style.width = `${pct}%`;
  estimateBanner.classList.remove("hidden");
}

// add assignment modal
addAssignmentBtn.addEventListener("click", () => {
  assignmentTitleInput.value = "";
  assignmentDueInput.value = "";
  assignmentModal.classList.remove("hidden");
  assignmentTitleInput.focus();
});

cancelAssignmentBtn.addEventListener("click", () => assignmentModal.classList.add("hidden"));

saveAssignmentBtn.addEventListener("click", async () => {
  const title = assignmentTitleInput.value.trim();
  if (!title) { alert("give your assignment a title 💛"); return; }
  if (!state.currentCourse) return;

  saveAssignmentBtn.disabled = true;
  try {
    await api("/projects", {
      method: "POST",
      body: JSON.stringify({
        courseId: state.currentCourse.id,
        title,
        dueDate: assignmentDueInput.value || null,
      }),
    });
    assignmentModal.classList.add("hidden");
    await loadAssignments(state.currentCourse.id);
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveAssignmentBtn.disabled = false;
  }
});

assignmentTitleInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveAssignmentBtn.click(); });


// ---------------------------------------------------------------------------
// PDF upload
// ---------------------------------------------------------------------------
[uploadPdfBtn, uploadPromptBtn].forEach(btn =>
  btn.addEventListener("click", () => pdfFileInput.click())
);

pdfFileInput.addEventListener("change", async () => {
  const file = pdfFileInput.files[0];
  if (!file || !state.currentProject) return;

  // show spinner over the split view area
  pdfSpinner.classList.remove("hidden");
  uploadPdfBtn.disabled = true;
  autoScheduleBtn.disabled = true;

  try {
    const formData = new FormData();
    formData.append("pdf", file);

    const result = await api(`/projects/${state.currentProject.id}/upload`, {
      method: "POST",
      body: formData,
    });

    // update local state
    state.currentProject.extractedText = result.extractedText;
    state.currentProject.totalEstimatedMinutes = result.totalEstimatedMinutes;
    state.tasks = result.tasks;

    renderAssignmentText(state.currentProject);
    renderTasks(result.tasks);
    renderEstimateBanner(result.tasks, state.currentProject);
  } catch (err) {
    alert(`couldn't process pdf: ${err.message}`);
  } finally {
    pdfSpinner.classList.add("hidden");
    uploadPdfBtn.disabled = false;
    autoScheduleBtn.disabled = false;
    pdfFileInput.value = "";
  }
});


// ---------------------------------------------------------------------------
// auto-schedule
// ---------------------------------------------------------------------------
autoScheduleBtn.addEventListener("click", async () => {
  if (!state.currentProject) return;
  if (!state.currentProject.dueDate) {
    alert("add a due date to this assignment first so margin knows when to work backwards from.");
    return;
  }

  autoScheduleBtn.disabled = true;
  autoScheduleBtn.textContent = "scheduling…";
  try {
    const result = await api(`/projects/${state.currentProject.id}/auto-schedule`, {
      method: "POST",
      body: JSON.stringify({}),
    });
    state.tasks = result.tasks;
    renderTasks(result.tasks);
    renderEstimateBanner(result.tasks, state.currentProject);
  } catch (err) {
    alert(`auto-schedule failed: ${err.message}`);
  } finally {
    autoScheduleBtn.disabled = false;
    autoScheduleBtn.textContent = "🗓 auto-schedule tasks";
  }
});


// ---------------------------------------------------------------------------
// tasks
// ---------------------------------------------------------------------------
function renderTasks(tasks) {
  taskList.innerHTML = "";

  if (!tasks.length) {
    taskList.appendChild(tasksEmpty);
    tasksEmpty.classList.remove("hidden");
    return;
  }

  tasksEmpty.classList.add("hidden");

  for (const task of tasks) {
    taskList.appendChild(buildTaskItem(task));
  }
}

function buildTaskItem(task) {
  const item = document.createElement("div");
  item.className = `task-item${task.status === "done" ? " done" : ""}`;
  item.dataset.taskId = task.id;

  // checkbox
  const checkbox = document.createElement("div");
  checkbox.className = `task-checkbox${task.status === "done" ? " checked" : ""}`;
  checkbox.textContent = task.status === "done" ? "✓" : "";
  checkbox.addEventListener("click", () => toggleTaskDone(task));
  item.appendChild(checkbox);

  const body = document.createElement("div");
  body.className = "task-body";

  // title
  const title = document.createElement("div");
  title.className = "task-title";
  title.textContent = task.title;
  body.appendChild(title);

  // tags row: due date + estimated time
  const tags = document.createElement("div");
  tags.className = "task-tags";

  if (task.dueDate) {
    const dueTag = document.createElement("span");
    dueTag.className = "task-due";
    const daysLeft = daysUntil(task.dueDate);
    dueTag.textContent = daysLeft === 0 ? "due today" : `due ${formatDate(task.dueDate)}`;
    if (daysLeft < 0) dueTag.classList.add("overdue");
    else if (daysLeft === 0) dueTag.classList.add("today");
    tags.appendChild(dueTag);
  }

  if (task.estimatedMinutes) {
    const est = document.createElement("span");
    est.className = "task-est";
    est.textContent = `~${task.estimatedMinutes}min`;
    tags.appendChild(est);
  }

  body.appendChild(tags);

  // time-spent row — always shown so users can log as they go
  const timeRow = document.createElement("div");
  timeRow.className = "time-spent-row";

  const timeLabel = document.createElement("label");
  timeLabel.textContent = "time spent:";

  const timeInput = document.createElement("input");
  timeInput.type = "number";
  timeInput.className = "time-spent-input";
  timeInput.min = "0";
  timeInput.placeholder = "min";
  timeInput.value = task.timeSpent != null ? task.timeSpent : "";

  timeInput.addEventListener("change", () => saveTimeSpent(task.id, timeInput.value));

  timeRow.appendChild(timeLabel);
  timeRow.appendChild(timeInput);
  body.appendChild(timeRow);

  item.appendChild(body);
  return item;
}

async function toggleTaskDone(task) {
  const newStatus = task.status === "done" ? "pending" : "done";
  try {
    const updated = await api(`/tasks/${task.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: newStatus }),
    });
    // update in state
    const idx = state.tasks.findIndex(t => t.id === task.id);
    if (idx !== -1) state.tasks[idx] = updated;
    // re-render just this item
    const el = taskList.querySelector(`[data-task-id="${task.id}"]`);
    if (el) el.replaceWith(buildTaskItem(updated));
    renderEstimateBanner(state.tasks, state.currentProject);
  } catch (err) {
    console.error("failed to toggle task:", err);
  }
}

async function saveTimeSpent(taskId, value) {
  const minutes = value === "" ? null : parseFloat(value);
  if (minutes !== null && (isNaN(minutes) || minutes < 0)) return;

  try {
    const updated = await api(`/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ timeSpent: minutes }),
    });
    const idx = state.tasks.findIndex(t => t.id === taskId);
    if (idx !== -1) state.tasks[idx] = updated;
    renderEstimateBanner(state.tasks, state.currentProject);
  } catch (err) {
    console.error("failed to save time:", err);
  }
}

// add task manually
addTaskBtn.addEventListener("click", () => {
  taskTitleInput.value = "";
  taskMinutesInput.value = "";
  taskModal.classList.remove("hidden");
  taskTitleInput.focus();
});

cancelTaskBtn.addEventListener("click", () => taskModal.classList.add("hidden"));

saveTaskBtn.addEventListener("click", async () => {
  const title = taskTitleInput.value.trim();
  if (!title) { alert("give the task a description 💛"); return; }
  if (!state.currentProject) return;

  saveTaskBtn.disabled = true;
  try {
    const task = await api("/tasks", {
      method: "POST",
      body: JSON.stringify({
        projectId: state.currentProject.id,
        title,
        estimatedMinutes: parseInt(taskMinutesInput.value) || 30,
      }),
    });
    taskModal.classList.add("hidden");
    state.tasks.push(task);
    renderTasks(state.tasks);
    renderEstimateBanner(state.tasks, state.currentProject);
    tasksEmpty.classList.add("hidden");
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveTaskBtn.disabled = false;
  }
});

taskTitleInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveTaskBtn.click(); });


// ---------------------------------------------------------------------------
// schedule editor
// ---------------------------------------------------------------------------
const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

scheduleBtn.addEventListener("click", async () => {
  scheduleBlocksList.innerHTML = "";
  try {
    const data = await api("/schedule");
    (data.blocks || []).forEach(addScheduleBlockRow);
  } catch (err) {
    console.error("failed to load schedule:", err);
  }
  scheduleModal.classList.remove("hidden");
});

function addScheduleBlockRow(block = {}) {
  const row = document.createElement("div");
  row.className = "schedule-block-row";

  const daySelect = document.createElement("select");
  DAYS.forEach((d, i) => {
    const opt = document.createElement("option");
    opt.value = i;
    opt.textContent = d;
    if (block.day !== undefined && parseInt(block.day) === i) opt.selected = true;
    daySelect.appendChild(opt);
  });

  const startInput = document.createElement("input");
  startInput.type = "time";
  startInput.value = block.startTime || "09:00";

  const endInput = document.createElement("input");
  endInput.type = "time";
  endInput.value = block.endTime || "11:00";

  const labelInput = document.createElement("input");
  labelInput.type = "text";
  labelInput.placeholder = "label (e.g. COMP 307)";
  labelInput.value = block.label || "";

  const removeBtn = document.createElement("button");
  removeBtn.className = "remove-block-btn";
  removeBtn.textContent = "✕";
  removeBtn.title = "remove";
  removeBtn.addEventListener("click", () => row.remove());

  row.appendChild(daySelect);
  row.appendChild(startInput);
  row.appendChild(endInput);
  row.appendChild(labelInput);
  row.appendChild(removeBtn);

  scheduleBlocksList.appendChild(row);
}

addBlockBtn.addEventListener("click", () => addScheduleBlockRow());

cancelScheduleBtn.addEventListener("click", () => scheduleModal.classList.add("hidden"));

saveScheduleBtn.addEventListener("click", async () => {
  const rows = scheduleBlocksList.querySelectorAll(".schedule-block-row");
  const blocks = Array.from(rows).map(row => {
    const inputs = row.querySelectorAll("select, input");
    return {
      day: parseInt(inputs[0].value),
      startTime: inputs[1].value,
      endTime: inputs[2].value,
      label: inputs[3].value.trim(),
    };
  });

  saveScheduleBtn.disabled = true;
  try {
    await api("/schedule", {
      method: "POST",
      body: JSON.stringify({ blocks }),
    });
    scheduleModal.classList.add("hidden");
  } catch (err) {
    alert(`couldn't save schedule: ${err.message}`);
  } finally {
    saveScheduleBtn.disabled = false;
  }
});


// ---------------------------------------------------------------------------
// date helpers
// ---------------------------------------------------------------------------
function formatDate(isoString) {
  if (!isoString) return "";
  // isoString might be "2026-05-15T00:00:00" or "2026-05-15"
  const d = new Date(isoString);
  return d.toLocaleDateString("en-US", { month: "short", day: "numeric" });
}

function daysUntil(isoString) {
  if (!isoString) return null;
  const due = new Date(isoString);
  due.setHours(0, 0, 0, 0);
  const now = new Date();
  now.setHours(0, 0, 0, 0);
  return Math.round((due - now) / (1000 * 60 * 60 * 24));
}

function formatMinutes(mins) {
  if (!mins) return "0min";
  if (mins < 60) return `${Math.round(mins)}min`;
  const h = Math.floor(mins / 60);
  const m = Math.round(mins % 60);
  return m > 0 ? `${h}h ${m}min` : `${h}h`;
}
