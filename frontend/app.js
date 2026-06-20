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
  sendPasswordResetEmail,
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
  currentTerm: null,          // { id, name }
  currentTermId: null,        // backend-stored "active" term id
  currentCourse: null,        // { id, name, color }
  currentProject: null,       // { id, title, dueDate, extractedText, totalEstimatedMinutes }
  tasks: [],                  // tasks for currentProject
  weekStartDay: 1,            // 1=Mon (default), 0=Sun
  workIntervalMinutes: 25,    // study session length
  darkMode: false,
};


// ---------------------------------------------------------------------------
// card ··· menu builder
// ---------------------------------------------------------------------------
function buildCardMenu(items) {
  const wrap = document.createElement("div");
  wrap.className = "card-menu-wrap";

  const btn = document.createElement("button");
  btn.className = "card-menu-btn";
  btn.textContent = "···";
  btn.title = "options";

  const dropdown = document.createElement("div");
  dropdown.className = "card-dropdown hidden";

  for (const item of items) {
    const el = document.createElement("button");
    el.className = "dropdown-item" + (item.danger ? " danger" : "");
    el.textContent = item.label;
    el.addEventListener("click", (e) => { e.stopPropagation(); dropdown.classList.add("hidden"); item.onClick(); });
    dropdown.appendChild(el);
  }

  btn.addEventListener("click", (e) => {
    e.stopPropagation();
    closeAllDropdowns();
    dropdown.classList.remove("hidden");
  });

  wrap.appendChild(btn);
  wrap.appendChild(dropdown);
  return wrap;
}

// ---------------------------------------------------------------------------
// DOM references
// ---------------------------------------------------------------------------
const signedOutView   = document.getElementById("signed-out-view");
const signedInView    = document.getElementById("signed-in-view");
const userEmailEl     = document.getElementById("user-email");
const signInBtn       = document.getElementById("sign-in-btn");
const signOutBtn      = document.getElementById("sign-out-btn");
const breadcrumb      = document.getElementById("breadcrumb");
const termSelect      = document.getElementById("term-select");
const termMenuBtn     = document.getElementById("term-menu-btn");
const termDropdown    = document.getElementById("term-dropdown");
const editTermBtn     = document.getElementById("edit-term-btn");
const deleteTermBtn   = document.getElementById("delete-term-btn");
const newTermBtn      = document.getElementById("new-term-btn");
const scheduleBtn     = document.getElementById("schedule-btn");
const profileBtn      = document.getElementById("profile-btn");
const profileDropdown = document.getElementById("profile-dropdown");
const profileInitials = document.getElementById("profile-initials");

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
const pdfFrame           = document.getElementById("pdf-frame");
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
const taskModalTitle     = document.getElementById("task-modal-title");
const taskTitleInput     = document.getElementById("task-title-input");
const taskMinutesInput   = document.getElementById("task-minutes-input");
const taskDueInput       = document.getElementById("task-due-input");
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
const viewHome        = document.getElementById("view-home");
const viewProfile     = document.getElementById("view-profile");
const viewGrades      = document.getElementById("view-grades");
const viewCalendar    = document.getElementById("view-calendar");
const viewProgress    = document.getElementById("view-progress");
const VIEWS = { home: viewHome, dashboard: viewDashboard, course: viewCourse, assignment: viewAssignment, grades: viewGrades, profile: viewProfile, calendar: viewCalendar, progress: viewProgress };

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

  const crumbLink = (label, onClick) => {
    const el = document.createElement("span");
    el.className = "breadcrumb-link";
    el.textContent = label;
    el.addEventListener("click", onClick);
    return el;
  };

  const crumbCurrent = (label) => {
    const el = document.createElement("span");
    el.className = "breadcrumb-current";
    el.textContent = label;
    return el;
  };

  const goHome = () => { showView("home"); loadHome(); };
  const goTerm = () => showView("dashboard");

  if (view === "home") return;
  if (view === "profile")  { breadcrumb.append(sep(), crumbCurrent("profile")); return; }
  if (view === "calendar") { breadcrumb.append(sep(), crumbCurrent("calendar")); return; }
  if (view === "progress") { breadcrumb.append(sep(), crumbCurrent("progress")); return; }

  if (view === "dashboard") {
    breadcrumb.append(sep(), crumbLink("home", goHome));
    if (state.currentTerm) breadcrumb.append(sep(), crumbCurrent(state.currentTerm.name));
    return;
  }

  if (view === "course" && state.currentCourse) {
    breadcrumb.append(
      sep(), crumbLink("home", goHome),
      sep(), crumbLink(state.currentTerm?.name || "term", goTerm),
      sep(), crumbCurrent(state.currentCourse.name),
    );
    return;
  }

  if (view === "grades" && state.currentCourse) {
    breadcrumb.append(
      sep(), crumbLink("home", goHome),
      sep(), crumbLink(state.currentTerm?.name || "term", goTerm),
      sep(), crumbLink(state.currentCourse.name, () => { showView("course"); loadAssignments(state.currentCourse.id); }),
      sep(), crumbCurrent("grade calculator"),
    );
    return;
  }

  if (view === "assignment" && state.currentCourse && state.currentProject) {
    breadcrumb.append(
      sep(), crumbLink("home", goHome),
      sep(), crumbLink(state.currentTerm?.name || "term", goTerm),
      sep(), crumbLink(state.currentCourse.name, () => { showView("course"); loadAssignments(state.currentCourse.id); }),
      sep(), crumbCurrent(state.currentProject.title),
    );
    return;
  }
}


// ---------------------------------------------------------------------------
// auth
// ---------------------------------------------------------------------------
// ---------------------------------------------------------------------------
// dropdown toggles
// ---------------------------------------------------------------------------
function toggleDropdown(btn, menu) {
  const isOpen = !menu.classList.contains("hidden");
  closeAllDropdowns();
  if (!isOpen) menu.classList.remove("hidden");
}

function closeAllDropdowns() {
  [termDropdown, profileDropdown].forEach(d => d?.classList.add("hidden"));
  document.querySelectorAll(".card-dropdown").forEach(d => d.classList.add("hidden"));
}

termMenuBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleDropdown(termMenuBtn, termDropdown); });
profileBtn.addEventListener("click", (e) => { e.stopPropagation(); toggleDropdown(profileBtn, profileDropdown); });
document.addEventListener("click", closeAllDropdowns);


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
    profileInitials.textContent = (user.displayName || user.email || "?")[0].toUpperCase();

    try {
      await api("/users/init", {
        method: "POST",
        body: JSON.stringify({ displayName: user.displayName || "" }),
      });
      // load week start preference before showing anything
      const meData = await api("/me");
      state.weekStartDay = meData.weekStartDay ?? 1;
      state.workIntervalMinutes = meData.workIntervalMinutes ?? 25;
      state.darkMode = meData.darkMode ?? false;
      applyTheme(state.darkMode);
      calWeekStart = getWeekStart(new Date());

      showView("home");
      await loadTerms();
      await loadHome();
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
// logo → home
// ---------------------------------------------------------------------------
document.getElementById("logo-btn").addEventListener("click", () => {
  showView("home");
  loadHome();
});

// ---------------------------------------------------------------------------
// home (terms grid)
// ---------------------------------------------------------------------------
const termsGrid    = document.getElementById("terms-grid");
const termsEmpty   = document.getElementById("terms-empty");
const addTermBtn   = document.getElementById("add-term-btn");

addTermBtn.addEventListener("click", async () => {
  const name = prompt("Name this term (e.g. Fall 2026):");
  if (!name?.trim()) return;
  try {
    const term = await api("/terms", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), setAsCurrent: true }),
    });
    state.currentTermId = term.id;
    await loadTerms();
    termSelect.value = term.id;
    await loadHome();
  } catch (err) {
    alert(`Couldn't create term: ${err.message}`);
  }
});

async function loadHome() {
  try {
    const [termsData, coursesData] = await Promise.all([
      api("/terms"),
      api("/courses?termId=all"),
    ]);
    renderHome(termsData.terms, coursesData.courses);
  } catch (err) {
    console.error("failed to load home:", err);
  }
}

function renderHome(terms, allCourses) {
  termsGrid.innerHTML = "";

  if (!terms.length) {
    termsGrid.appendChild(termsEmpty);
    termsEmpty.classList.remove("hidden");
    return;
  }

  termsEmpty.classList.add("hidden");

  // count courses per term
  const countByTerm = {};
  for (const c of allCourses) {
    countByTerm[c.termId] = (countByTerm[c.termId] || 0) + 1;
  }

  for (const term of terms) {
    const card = document.createElement("div");
    card.className = "term-card";

    const top = document.createElement("div");
    top.className = "term-card-top";

    const name = document.createElement("div");
    name.className = "term-card-name";
    name.textContent = term.name;
    top.appendChild(name);

    if (term.id === state.currentTermId) {
      const badge = document.createElement("span");
      badge.className = "term-active-badge";
      badge.textContent = "current";
      top.appendChild(badge);
    }

    card.appendChild(top);

    const meta = document.createElement("div");
    meta.className = "term-card-meta";
    const count = countByTerm[term.id] || 0;
    meta.textContent = count === 1 ? "1 course" : `${count} courses`;
    card.appendChild(meta);

    // ··· menu
    const menuItems = [
      { label: "Rename", onClick: () => renameTerm(term) },
    ];
    if (term.id !== state.currentTermId) {
      menuItems.push({ label: "Set as current", onClick: () => setCurrentTerm(term) });
    }
    menuItems.push({ label: "Delete", danger: true, onClick: () => deleteTerm(term) });
    card.appendChild(buildCardMenu(menuItems));

    card.addEventListener("click", () => openTerm(term));
    termsGrid.appendChild(card);
  }
}

async function openTerm(term) {
  state.currentTerm = term;
  termSelect.value = term.id;
  document.getElementById("course-title").textContent = term.name;
  showView("dashboard");
  await loadCourses(term.id);
}

async function setCurrentTerm(term) {
  try {
    await api(`/terms/${term.id}`, { method: "PATCH", body: JSON.stringify({ setAsCurrent: true }) });
    state.currentTermId = term.id;
    termSelect.value = term.id;
    await loadHome();
  } catch (err) {
    alert(`Couldn't set current term: ${err.message}`);
  }
}

async function renameTerm(term) {
  const name = prompt("Rename term:", term.name);
  if (!name?.trim() || name.trim() === term.name) return;
  try {
    await api(`/terms/${term.id}`, { method: "PATCH", body: JSON.stringify({ name: name.trim() }) });
    await loadTerms();
    await loadHome();
  } catch (err) {
    alert(`Couldn't rename: ${err.message}`);
  }
}

async function deleteTerm(term) {
  if (!confirm(`Delete "${term.name}" and all its courses and assignments? This can't be undone.`)) return;
  try {
    await api(`/terms/${term.id}`, { method: "DELETE" });
    await loadTerms();
    await loadHome();
  } catch (err) {
    alert(`Couldn't delete: ${err.message}`);
  }
}

// ---------------------------------------------------------------------------
// terms (header select)
// ---------------------------------------------------------------------------
async function loadTerms() {
  try {
    const data = await api("/terms");
    state.currentTermId = data.currentTermId || null;
    renderTermSelect(data.terms);
  } catch (err) {
    console.error("failed to load terms:", err);
  }
}

function renderTermSelect(terms) {
  termSelect.innerHTML = "";
  for (const term of terms) {
    const opt = document.createElement("option");
    opt.value = term.id;
    opt.textContent = term.name;
    termSelect.appendChild(opt);
  }
  // select the current term if set, otherwise the first
  if (state.currentTermId) termSelect.value = state.currentTermId;
}

termSelect.addEventListener("change", async () => {
  const termId = termSelect.value;
  if (!termId) return;
  // update the backend's active term
  try {
    await api(`/terms/${termId}`, { method: "PATCH", body: JSON.stringify({ setAsCurrent: true }) });
    state.currentTermId = termId;
    // refresh other views if open
    if (!viewHome.classList.contains("hidden")) await loadHome();
    if (!viewCalendar.classList.contains("hidden")) await loadCalendar();
    if (!viewProgress.classList.contains("hidden")) await loadProgress();
  } catch (err) {
    console.error("failed to set current term:", err);
  }
  showView("dashboard");
  await loadCourses(termId);
});

newTermBtn.addEventListener("click", async () => {
  const name = prompt("name this term (e.g. Fall 2026):");
  if (!name || !name.trim()) return;
  try {
    const term = await api("/terms", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), setAsCurrent: true }),
    });
    state.currentTermId = term.id;
    await loadTerms();
    termSelect.value = term.id;
    showView("dashboard");
    await loadCourses(term.id);
  } catch (err) {
    alert(`couldn't create term: ${err.message}`);
  }
});

editTermBtn.addEventListener("click", async () => {
  const termId = termSelect.value;
  if (!termId) return;
  const current = termSelect.options[termSelect.selectedIndex]?.text || "";
  const name = prompt("rename term:", current);
  if (!name || !name.trim() || name.trim() === current) return;
  try {
    await api(`/terms/${termId}`, {
      method: "PATCH",
      body: JSON.stringify({ name: name.trim() }),
    });
    await loadTerms();
    termSelect.value = termId;
  } catch (err) {
    alert(`couldn't rename: ${err.message}`);
  }
});

deleteTermBtn.addEventListener("click", async () => {
  const termId = termSelect.value;
  if (!termId) return;
  const name = termSelect.options[termSelect.selectedIndex]?.text || "this term";
  if (!confirm(`Delete "${name}" and all its courses and assignments? This can't be undone.`)) return;
  try {
    await api(`/terms/${termId}`, { method: "DELETE" });
    await loadTerms();
    await loadCourses();
  } catch (err) {
    alert(`couldn't delete: ${err.message}`);
  }
});

// ---------------------------------------------------------------------------
// courses
// ---------------------------------------------------------------------------
async function loadCourses(termId) {
  try {
    const query = termId ? `?termId=${termId}` : "";
    const data = await api(`/courses${query}`);
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

    card.appendChild(buildCardMenu([
      { label: "Edit", onClick: () => editCourse(course) },
      { label: "Delete", danger: true, onClick: () => deleteCourse(course) },
    ]));

    card.addEventListener("click", () => openCourse(course));
    coursesList.appendChild(card);
  }
}

async function openCourse(course) {
  state.currentCourse = course;
  courseTitle.textContent = course.name;
  // clear stale count from previous course
  document.getElementById("course-completion-count")?.remove();
  showView("course");
  await loadAssignments(course.id);
}

// add / edit course modal
let editingCourseId = null;

function openCourseModal(course = null) {
  editingCourseId = course ? course.id : null;
  courseNameInput.value = course ? course.name : "";
  courseColorInput.value = course ? course.color : "#a8d5ba";
  courseModal.querySelector("h3").textContent = course ? "edit course" : "add a course";
  courseModal.classList.remove("hidden");
  courseNameInput.focus();
}

addCourseBtn.addEventListener("click", () => openCourseModal());
cancelCourseBtn.addEventListener("click", () => courseModal.classList.add("hidden"));

saveCourseBtn.addEventListener("click", async () => {
  const name = courseNameInput.value.trim();
  if (!name) { alert("give your course a name 💛"); return; }
  saveCourseBtn.disabled = true;
  try {
    if (editingCourseId) {
      await api(`/courses/${editingCourseId}`, {
        method: "PATCH",
        body: JSON.stringify({ name, color: courseColorInput.value }),
      });
    } else {
      await api("/courses", {
        method: "POST",
        body: JSON.stringify({ name, color: courseColorInput.value }),
      });
    }
    courseModal.classList.add("hidden");
    await loadCourses(termSelect.value || undefined);
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveCourseBtn.disabled = false;
    editingCourseId = null;
  }
});

courseNameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveCourseBtn.click(); });

async function editCourse(course) {
  openCourseModal(course);
}

async function deleteCourse(course) {
  if (!confirm(`Delete "${course.name}" and all its assignments? This can't be undone.`)) return;
  try {
    await api(`/courses/${course.id}`, { method: "DELETE" });
    await loadCourses(termSelect.value || undefined);
  } catch (err) {
    alert(`couldn't delete: ${err.message}`);
  }
}


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

  // completion count in course header
  const completed = projects.filter(p => p.status === "completed").length;
  let countEl = document.getElementById("course-completion-count");
  if (!countEl) {
    countEl = document.createElement("span");
    countEl.id = "course-completion-count";
    countEl.className = "course-completion";
    courseTitle.parentElement.appendChild(countEl);
  }
  countEl.textContent = `${completed} / ${projects.length} done`;

  for (const project of projects) {
    const card = document.createElement("div");
    card.className = `assignment-card${project.status === "completed" ? " completed" : ""}`;

    const colorBar = document.createElement("div");
    colorBar.className = "assignment-card-color";
    colorBar.style.setProperty("--course-color", state.currentCourse?.color || "#ccc");
    card.appendChild(colorBar);

    const body = document.createElement("div");
    body.className = "assignment-card-body";

    const titleRow = document.createElement("div");
    titleRow.style.display = "flex";
    titleRow.style.alignItems = "center";
    titleRow.style.gap = "0.5rem";

    const title = document.createElement("div");
    title.className = "assignment-card-title";
    title.textContent = project.title;
    titleRow.appendChild(title);

    if (project.status === "completed") {
      const badge = document.createElement("span");
      badge.className = "assignment-done-badge";
      badge.textContent = "✓ done";
      titleRow.appendChild(badge);
    }

    body.appendChild(titleRow);

    const meta = document.createElement("div");
    meta.className = "assignment-card-meta";
    if (project.dueDate) {
      const span = document.createElement("span");
      span.textContent = `due ${formatDate(project.dueDate)}`;
      meta.appendChild(span);
    }
    if (project.totalEstimatedMinutes) {
      const est = document.createElement("span");
      est.textContent = `~${formatMinutes(project.totalEstimatedMinutes)}`;
      meta.appendChild(est);
    }
    body.appendChild(meta);
    card.appendChild(body);

    const isDone = project.status === "completed";
    card.appendChild(buildCardMenu([
      { label: isDone ? "Mark as active" : "Mark as complete", onClick: () => toggleAssignmentDone(project) },
      { label: "Edit", onClick: () => openAssignmentModal(project) },
      { label: "Delete", danger: true, onClick: () => deleteAssignment(project) },
    ]));

    card.addEventListener("click", () => openAssignment(project));
    assignmentsList.appendChild(card);
  }
}

async function openAssignment(project) {
  state.currentProject = { ...project, pdfUrl: null };
  state.tasks = [];
  showView("assignment");
  viewAssignment.style.setProperty("--course-color", state.currentCourse?.color || "#ccc");
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
  if (project.pdfUrl) {
    textEmpty.classList.add("hidden");
    textContent.classList.add("hidden");
    pdfFrame.src = project.pdfUrl;
    pdfFrame.classList.remove("hidden");
  } else if (project.extractedText) {
    textEmpty.classList.add("hidden");
    pdfFrame.classList.add("hidden");
    textContent.classList.remove("hidden");
    textContent.textContent = project.extractedText;
  } else {
    pdfFrame.classList.add("hidden");
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

// add / edit assignment modal
let editingProjectId = null;

function openAssignmentModal(project = null) {
  editingProjectId = project ? project.id : null;
  assignmentTitleInput.value = project ? project.title : "";
  assignmentDueInput.value = project?.dueDate ? project.dueDate.slice(0, 10) : "";
  assignmentModal.querySelector("h3").textContent = project ? "edit assignment" : "add an assignment";
  assignmentModal.classList.remove("hidden");
  assignmentTitleInput.focus();
}

addAssignmentBtn.addEventListener("click", () => openAssignmentModal());
cancelAssignmentBtn.addEventListener("click", () => assignmentModal.classList.add("hidden"));

saveAssignmentBtn.addEventListener("click", async () => {
  const title = assignmentTitleInput.value.trim();
  if (!title) { alert("give your assignment a title 💛"); return; }
  if (!state.currentCourse) return;

  saveAssignmentBtn.disabled = true;
  try {
    if (editingProjectId) {
      await api(`/projects/${editingProjectId}`, {
        method: "PATCH",
        body: JSON.stringify({ title, dueDate: assignmentDueInput.value || null }),
      });
    } else {
      await api("/projects", {
        method: "POST",
        body: JSON.stringify({
          courseId: state.currentCourse.id,
          title,
          dueDate: assignmentDueInput.value || null,
        }),
      });
    }
    assignmentModal.classList.add("hidden");
    await loadAssignments(state.currentCourse.id);
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveAssignmentBtn.disabled = false;
    editingProjectId = null;
  }
});

assignmentTitleInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveAssignmentBtn.click(); });

async function deleteAssignment(project) {
  if (!confirm(`Delete "${project.title}" and all its tasks? This can't be undone.`)) return;
  try {
    await api(`/projects/${project.id}`, { method: "DELETE" });
    await loadAssignments(state.currentCourse.id);
  } catch (err) {
    alert(`couldn't delete: ${err.message}`);
  }
}

async function toggleAssignmentDone(project) {
  const newStatus = project.status === "completed" ? "active" : "completed";
  try {
    await api(`/projects/${project.id}`, {
      method: "PATCH",
      body: JSON.stringify({ status: newStatus }),
    });
    await loadAssignments(state.currentCourse.id);
  } catch (err) {
    alert(`couldn't update: ${err.message}`);
  }
}


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
    state.currentProject.pdfUrl = URL.createObjectURL(file);
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
  // timer start button
  const timerBtn = document.createElement("button");
  timerBtn.className = "task-timer-btn";
  timerBtn.dataset.taskId = task.id;
  timerBtn.title = "start study session";
  timerBtn.textContent = (task.id === timer.taskId && timer.running) ? "⏸" : "▶";
  if (task.id === timer.taskId && timer.running) timerBtn.classList.add("active");
  timerBtn.addEventListener("click", () => startTimerForTask(task));
  tags.appendChild(timerBtn);

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

  // ··· menu
  item.appendChild(buildCardMenu([
    { label: "Edit", onClick: () => openTaskModal(task) },
    { label: "Delete", danger: true, onClick: () => deleteTask(task) },
  ]));

  return item;
}

async function deleteTask(task) {
  if (!confirm(`Delete "${task.title}"? This can't be undone.`)) return;
  try {
    await api(`/tasks/${task.id}`, { method: "DELETE" });
    state.tasks = state.tasks.filter(t => t.id !== task.id);
    renderTasks(state.tasks);
    renderEstimateBanner(state.tasks, state.currentProject);
  } catch (err) {
    alert(`couldn't delete: ${err.message}`);
  }
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

// add / edit task
let editingTaskId = null;

let taskModalFromCalendar = false;

function openTaskModal(task = null, opts = {}) {
  editingTaskId = task ? task.id : null;
  taskModalFromCalendar = opts.fromCalendar || false;
  taskModalTitle.textContent = task ? "Edit task" : "Add a task";
  taskTitleInput.value = task ? task.title : "";
  taskMinutesInput.value = task ? (task.estimatedMinutes || "") : "";
  taskDueInput.value = task?.dueDate ? task.dueDate.slice(0, 10) : new Date().toLocaleDateString("en-CA");
  taskModal.classList.remove("hidden");
  taskTitleInput.focus();
}

addTaskBtn.addEventListener("click", () => openTaskModal());
cancelTaskBtn.addEventListener("click", () => taskModal.classList.add("hidden"));

saveTaskBtn.addEventListener("click", async () => {
  const title = taskTitleInput.value.trim();
  if (!title) { alert("give the task a description 💛"); return; }
  if (!state.currentProject) return;

  saveTaskBtn.disabled = true;
  try {
    if (editingTaskId) {
      const body = {
        title,
        estimatedMinutes: parseInt(taskMinutesInput.value) || null,
        dueDate: taskDueInput.value || null,
      };
      const updated = await api(`/tasks/${editingTaskId}`, {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      const idx = state.tasks.findIndex(t => t.id === editingTaskId);
      if (idx !== -1) state.tasks[idx] = updated;
      const calIdx = calTasks.findIndex(t => t.id === editingTaskId);
      if (calIdx !== -1) calTasks[calIdx] = { ...calTasks[calIdx], ...updated };
    } else {
      const task = await api("/tasks", {
        method: "POST",
        body: JSON.stringify({
          projectId: state.currentProject.id,
          title,
          estimatedMinutes: parseInt(taskMinutesInput.value) || 30,
          dueDate: taskDueInput.value || null,
        }),
      });
      state.tasks.push(task);
    }
    taskModal.classList.add("hidden");
    if (taskModalFromCalendar) {
      renderCalendar();
    } else {
      renderTasks(state.tasks);
      renderEstimateBanner(state.tasks, state.currentProject);
    }
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveTaskBtn.disabled = false;
    editingTaskId = null;
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
// grade calculator
// ---------------------------------------------------------------------------
const gradeCalcBtn    = document.getElementById("grade-calc-btn");
const gradesCourseTitle = document.getElementById("grades-course-title");
const editSchemeBtn   = document.getElementById("edit-scheme-btn");
const categoriesList  = document.getElementById("categories-list");
const gradesEmpty     = document.getElementById("grades-empty");
const gradeLetter     = document.getElementById("grade-letter");
const gradePct        = document.getElementById("grade-pct");
const targetInput     = document.getElementById("target-input");
const targetResult    = document.getElementById("target-result");

const schemeModal     = document.getElementById("scheme-modal");
const schemeRows      = document.getElementById("scheme-rows");
const addCategoryBtn  = document.getElementById("add-category-btn");
const cancelSchemeBtn = document.getElementById("cancel-scheme-btn");
const saveSchemeBtn   = document.getElementById("save-scheme-btn");
const schemeWeightTotal = document.getElementById("scheme-weight-total");

const gradeModal      = document.getElementById("grade-modal");
const gradeModalTitle = document.getElementById("grade-modal-title");
const gradeNameInput  = document.getElementById("grade-name-input");
const gradeScoreInput = document.getElementById("grade-score-input");
const gradeTotalInput = document.getElementById("grade-total-input");
const cancelGradeBtn  = document.getElementById("cancel-grade-btn");
const saveGradeBtn    = document.getElementById("save-grade-btn");

let gradesState = { categories: [], grades: [] };
let editingGrade = null; // { id, categoryId } when editing an existing entry

gradeCalcBtn.addEventListener("click", async () => {
  if (!state.currentCourse) return;
  gradesCourseTitle.textContent = `${state.currentCourse.name} — grade calculator`;
  showView("grades");
  await loadGrades();
});

async function loadGrades() {
  try {
    const data = await api(`/courses/${state.currentCourse.id}/grading`);
    gradesState = data;
    renderGrades();
  } catch (err) {
    console.error("failed to load grades:", err);
  }
}

function renderGrades() {
  const { categories, grades } = gradesState;
  categoriesList.innerHTML = "";

  if (!categories.length) {
    gradesEmpty.classList.remove("hidden");
    gradePct.textContent = "—";
    return;
  }

  gradesEmpty.classList.add("hidden");

  // compute per-category averages
  const catAverages = {};
  for (const cat of categories) {
    const entries = grades.filter(g => g.categoryId === cat.id);
    if (entries.length) {
      const avg = entries.reduce((s, g) => s + (g.score / g.total) * 100, 0) / entries.length;
      catAverages[cat.id] = avg;
    }
  }

  // overall weighted grade (only count categories that have entries)
  const totalWeight = categories.reduce((s, c) => s + c.weight, 0);
  let weightedSum = 0;
  let coveredWeight = 0;
  for (const cat of categories) {
    if (catAverages[cat.id] !== undefined) {
      weightedSum += (cat.weight / totalWeight) * catAverages[cat.id];
      coveredWeight += cat.weight;
    }
  }

  const currentGrade = coveredWeight > 0 ? (weightedSum * totalWeight / coveredWeight) : null;

  if (currentGrade !== null) {
    gradePct.textContent = `${currentGrade.toFixed(1)}%`;
  } else {
    gradePct.textContent = "—";
  }

  updateTargetResult(currentGrade, categories, catAverages, totalWeight);

  // render each category block
  for (const cat of categories) {
    const entries = grades.filter(g => g.categoryId === cat.id);
    const avg = catAverages[cat.id];

    const block = document.createElement("div");
    block.className = "category-block";

    // header
    const header = document.createElement("div");
    header.className = "category-header";

    const left = document.createElement("div");
    left.className = "category-header-left";

    const nameEl = document.createElement("span");
    nameEl.className = "category-name";
    nameEl.textContent = cat.name;

    const weightEl = document.createElement("span");
    weightEl.className = "category-weight";
    weightEl.textContent = `${cat.weight}%`;

    left.appendChild(nameEl);
    left.appendChild(weightEl);

    const avgEl = document.createElement("span");
    avgEl.className = "category-avg";
    avgEl.textContent = avg !== undefined ? `avg: ${avg.toFixed(1)}%` : "no grades yet";

    header.appendChild(left);
    header.appendChild(avgEl);
    block.appendChild(header);

    // grade entries
    const entriesEl = document.createElement("div");
    entriesEl.className = "grade-entries";

    for (const entry of entries) {
      entriesEl.appendChild(buildGradeEntryRow(entry, cat.id));
    }

    // add grade button
    const addRow = document.createElement("div");
    addRow.className = "add-grade-row";
    const addBtn = document.createElement("button");
    addBtn.className = "text-btn small";
    addBtn.textContent = "+ add grade";
    addBtn.addEventListener("click", () => openGradeModal(cat.id));
    addRow.appendChild(addBtn);
    entriesEl.appendChild(addRow);

    block.appendChild(entriesEl);
    categoriesList.appendChild(block);
  }
}

function buildGradeEntryRow(entry, categoryId) {
  const row = document.createElement("div");
  row.className = "grade-entry-row";

  const name = document.createElement("span");
  name.className = "grade-entry-name";
  name.textContent = entry.name;

  const score = document.createElement("span");
  score.className = "grade-entry-score";
  score.textContent = `${entry.score} / ${entry.total}`;

  const pct = document.createElement("span");
  pct.className = "grade-entry-pct";
  pct.textContent = `${((entry.score / entry.total) * 100).toFixed(1)}%`;

  const actions = document.createElement("div");
  actions.className = "grade-entry-actions";

  const editBtn = document.createElement("button");
  editBtn.className = "card-action-btn";
  editBtn.textContent = "✏️";
  editBtn.addEventListener("click", () => openGradeModal(categoryId, entry));

  const delBtn = document.createElement("button");
  delBtn.className = "card-action-btn delete";
  delBtn.textContent = "🗑";
  delBtn.addEventListener("click", () => deleteGradeEntry(entry));

  actions.appendChild(editBtn);
  actions.appendChild(delBtn);

  row.appendChild(name);
  row.appendChild(score);
  row.appendChild(pct);
  row.appendChild(actions);
  return row;
}

function openGradeModal(categoryId, entry = null) {
  editingGrade = entry ? { id: entry.id, categoryId } : { id: null, categoryId };
  gradeModalTitle.textContent = entry ? "edit grade" : "add grade";
  gradeNameInput.value = entry ? entry.name : "";
  gradeScoreInput.value = entry ? entry.score : "";
  gradeTotalInput.value = entry ? entry.total : "100";
  gradeModal.classList.remove("hidden");
  gradeNameInput.focus();
}

cancelGradeBtn.addEventListener("click", () => gradeModal.classList.add("hidden"));

saveGradeBtn.addEventListener("click", async () => {
  const name = gradeNameInput.value.trim();
  const score = parseFloat(gradeScoreInput.value);
  const total = parseFloat(gradeTotalInput.value);

  if (!name) { alert("give this grade a name 💛"); return; }
  if (isNaN(score) || isNaN(total) || total <= 0) { alert("enter a valid score and total"); return; }

  saveGradeBtn.disabled = true;
  try {
    if (editingGrade.id) {
      const updated = await api(`/courses/${state.currentCourse.id}/grades/${editingGrade.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name, score, total }),
      });
      gradesState.grades = gradesState.grades.map(g => g.id === updated.id ? updated : g);
    } else {
      const created = await api(`/courses/${state.currentCourse.id}/grades`, {
        method: "POST",
        body: JSON.stringify({ categoryId: editingGrade.categoryId, name, score, total }),
      });
      gradesState.grades.push(created);
    }
    gradeModal.classList.add("hidden");
    renderGrades();
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveGradeBtn.disabled = false;
    editingGrade = null;
  }
});

gradeNameInput.addEventListener("keydown", (e) => { if (e.key === "Enter") saveGradeBtn.click(); });

async function deleteGradeEntry(entry) {
  if (!confirm(`Delete "${entry.name}"?`)) return;
  try {
    await api(`/courses/${state.currentCourse.id}/grades/${entry.id}`, { method: "DELETE" });
    gradesState.grades = gradesState.grades.filter(g => g.id !== entry.id);
    renderGrades();
  } catch (err) {
    alert(`couldn't delete: ${err.message}`);
  }
}

// target grade calculator
targetInput.addEventListener("input", () => {
  const { categories, grades } = gradesState;
  const totalWeight = categories.reduce((s, c) => s + c.weight, 0);
  const catAverages = {};
  for (const cat of categories) {
    const entries = grades.filter(g => g.categoryId === cat.id);
    if (entries.length) {
      catAverages[cat.id] = entries.reduce((s, g) => s + (g.score / g.total) * 100, 0) / entries.length;
    }
  }
  let weightedSum = 0, coveredWeight = 0;
  for (const cat of categories) {
    if (catAverages[cat.id] !== undefined) {
      weightedSum += (cat.weight / totalWeight) * catAverages[cat.id];
      coveredWeight += cat.weight;
    }
  }
  const currentGrade = coveredWeight > 0 ? (weightedSum * totalWeight / coveredWeight) : null;
  updateTargetResult(currentGrade, categories, catAverages, totalWeight);
});

function updateTargetResult(currentGrade, categories, catAverages, totalWeight) {
  const target = parseFloat(targetInput.value);
  if (isNaN(target) || !categories.length) { targetResult.textContent = ""; return; }

  const remaining = categories.filter(c => catAverages[c.id] === undefined);
  const remainingWeight = remaining.reduce((s, c) => s + c.weight, 0);

  if (remainingWeight === 0) {
    targetResult.textContent = currentGrade >= target
      ? "you've already hit your target 🎉"
      : "all grades are in — final grade is set.";
    return;
  }

  // points already locked in
  let lockedPoints = 0;
  for (const cat of categories) {
    if (catAverages[cat.id] !== undefined) {
      lockedPoints += (cat.weight / totalWeight) * catAverages[cat.id];
    }
  }

  // need: target = lockedPoints + (remainingWeight/totalWeight) * neededAvg
  const neededAvg = ((target - lockedPoints * totalWeight / totalWeight) / (remainingWeight / totalWeight));

  if (neededAvg <= 0) {
    targetResult.textContent = "you've already locked in enough to hit your target 🎉";
  } else if (neededAvg > 100) {
    targetResult.textContent = `you'd need ${neededAvg.toFixed(1)}% on remaining work — the target may not be reachable.`;
  } else {
    const catNames = remaining.map(c => c.name).join(", ");
    targetResult.textContent = `you need ~${neededAvg.toFixed(1)}% average on: ${catNames}.`;
  }
}

// scheme editor
editSchemeBtn.addEventListener("click", () => {
  schemeRows.innerHTML = "";
  for (const cat of gradesState.categories) {
    addSchemeRow(cat);
  }
  if (!gradesState.categories.length) addSchemeRow();
  updateWeightTotal();
  schemeModal.classList.remove("hidden");
});

function addSchemeRow(cat = {}) {
  const row = document.createElement("div");
  row.className = "scheme-row";

  const nameInput = document.createElement("input");
  nameInput.type = "text";
  nameInput.placeholder = "category name";
  nameInput.value = cat.name || "";

  const weightInput = document.createElement("input");
  weightInput.type = "number";
  weightInput.min = "0";
  weightInput.max = "100";
  weightInput.placeholder = "%";
  weightInput.value = cat.weight !== undefined ? cat.weight : "";
  weightInput.addEventListener("input", updateWeightTotal);

  const removeBtn = document.createElement("button");
  removeBtn.className = "remove-block-btn";
  removeBtn.textContent = "✕";
  removeBtn.addEventListener("click", () => { row.remove(); updateWeightTotal(); });

  row.appendChild(nameInput);
  row.appendChild(weightInput);
  row.appendChild(removeBtn);
  schemeRows.appendChild(row);
}

function updateWeightTotal() {
  const rows = schemeRows.querySelectorAll(".scheme-row");
  const total = Array.from(rows).reduce((s, row) => {
    const val = parseFloat(row.querySelectorAll("input")[1].value) || 0;
    return s + val;
  }, 0);
  schemeWeightTotal.textContent = `total: ${total}%`;
  schemeWeightTotal.className = "weight-total " + (total === 100 ? "ok" : total > 100 ? "over" : "under");
}

addCategoryBtn.addEventListener("click", () => { addSchemeRow(); updateWeightTotal(); });
cancelSchemeBtn.addEventListener("click", () => schemeModal.classList.add("hidden"));

saveSchemeBtn.addEventListener("click", async () => {
  const rows = schemeRows.querySelectorAll(".scheme-row");
  const categories = Array.from(rows).map((row, i) => {
    const inputs = row.querySelectorAll("input");
    return {
      id: gradesState.categories[i]?.id || `cat_${Date.now()}_${i}`,
      name: inputs[0].value.trim(),
      weight: parseFloat(inputs[1].value) || 0,
    };
  }).filter(c => c.name);

  const total = categories.reduce((s, c) => s + c.weight, 0);
  if (Math.abs(total - 100) > 0.01) {
    alert(`weights add up to ${total}% — they need to equal 100%.`);
    return;
  }

  saveSchemeBtn.disabled = true;
  try {
    const data = await api(`/courses/${state.currentCourse.id}/grading`, {
      method: "POST",
      body: JSON.stringify({ categories }),
    });
    gradesState.categories = data.categories;
    schemeModal.classList.add("hidden");
    renderGrades();
  } catch (err) {
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveSchemeBtn.disabled = false;
  }
});

function pctToLetter(pct) {
  if (pct >= 93) return "A";
  if (pct >= 90) return "A−";
  if (pct >= 87) return "B+";
  if (pct >= 83) return "B";
  if (pct >= 80) return "B−";
  if (pct >= 77) return "C+";
  if (pct >= 73) return "C";
  if (pct >= 70) return "C−";
  if (pct >= 67) return "D+";
  if (pct >= 60) return "D";
  return "F";
}

// ---------------------------------------------------------------------------
// profile page
// ---------------------------------------------------------------------------
const profilePageBtn   = document.getElementById("profile-page-btn");
const profileNameInput = document.getElementById("profile-name-input");
const profileEmailDisp = document.getElementById("profile-email-display");
const saveProfileBtn   = document.getElementById("save-profile-btn");
const profileSaveMsg   = document.getElementById("profile-save-msg");
const resetPwBtn       = document.getElementById("reset-pw-btn");
const resetPwMsg       = document.getElementById("reset-pw-msg");
const darkModeToggle   = document.getElementById("dark-mode-toggle");
const weekStartSelect  = document.getElementById("week-start-select");
const workIntervalInput = document.getElementById("work-interval-input");
const savePrefsBtn     = document.getElementById("save-prefs-btn");
const prefsSaveMsg     = document.getElementById("prefs-save-msg");

profilePageBtn.addEventListener("click", async () => {
  closeAllDropdowns();
  showView("profile");
  try {
    const data = await api("/me");
    profileNameInput.value = data.displayName || "";
    profileEmailDisp.value = data.email || "";
    darkModeToggle.checked = data.darkMode ?? false;
    weekStartSelect.value = String(data.weekStartDay ?? 1);
    workIntervalInput.value = data.workIntervalMinutes ?? 25;
  } catch (err) {
    console.error("failed to load profile:", err);
  }
});

saveProfileBtn.addEventListener("click", async () => {
  const displayName = profileNameInput.value.trim();
  saveProfileBtn.disabled = true;
  profileSaveMsg.classList.add("hidden");
  try {
    await api("/me", {
      method: "PATCH",
      body: JSON.stringify({ displayName }),
    });
    showMsg(profileSaveMsg, "Changes saved.", "success");
  } catch (err) {
    showMsg(profileSaveMsg, `Couldn't save: ${err.message}`, "error");
  } finally {
    saveProfileBtn.disabled = false;
  }
});

// toggle dark mode instantly without waiting for save
darkModeToggle.addEventListener("change", () => applyTheme(darkModeToggle.checked));

function applyTheme(dark) {
  document.documentElement.setAttribute("data-theme", dark ? "dark" : "light");
}

savePrefsBtn.addEventListener("click", async () => {
  const darkMode = darkModeToggle.checked;
  const weekStartDay = parseInt(weekStartSelect.value);
  const workIntervalMinutes = parseInt(workIntervalInput.value) || 25;
  savePrefsBtn.disabled = true;
  prefsSaveMsg.classList.add("hidden");
  try {
    await api("/me", { method: "PATCH", body: JSON.stringify({ darkMode, weekStartDay, workIntervalMinutes }) });
    state.darkMode = darkMode;
    state.weekStartDay = weekStartDay;
    state.workIntervalMinutes = workIntervalMinutes;
    calWeekStart = getWeekStart(new Date());
    showMsg(prefsSaveMsg, "Preferences saved.", "success");
  } catch (err) {
    showMsg(prefsSaveMsg, `Couldn't save: ${err.message}`, "error");
  } finally {
    savePrefsBtn.disabled = false;
  }
});

resetPwBtn.addEventListener("click", async () => {
  const user = auth.currentUser;
  if (!user?.email) return;
  resetPwBtn.disabled = true;
  resetPwMsg.classList.add("hidden");
  try {
    await sendPasswordResetEmail(auth, user.email);
    showMsg(resetPwMsg, `Reset email sent to ${user.email}.`, "success");
  } catch (err) {
    showMsg(resetPwMsg, `Couldn't send email: ${err.message}`, "error");
  } finally {
    resetPwBtn.disabled = false;
  }
});

function showMsg(el, text, type) {
  el.textContent = text;
  el.className = `profile-msg ${type}`;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 5000);
}

// ---------------------------------------------------------------------------
// progress dashboard
// ---------------------------------------------------------------------------
const progressBtn  = document.getElementById("progress-btn");
const progressGrid = document.getElementById("progress-grid");

progressBtn.addEventListener("click", async () => {
  showView("progress");
  renderBreadcrumb("progress");
  await loadProgress();
});

async function loadProgress() {
  const termId = termSelect.value || state.currentTermId;
  try {
    const data = await api(`/calendar${termId ? `?termId=${termId}` : ""}`);
    renderProgress(data.tasks);
  } catch (err) {
    console.error("failed to load progress:", err);
  }
}

function renderProgress(tasks) {
  progressGrid.innerHTML = "";

  if (!tasks.length) {
    const empty = document.createElement("p");
    empty.className = "empty-state progress-empty";
    empty.textContent = "No tasks yet — add some assignments to track your progress 💛";
    progressGrid.appendChild(empty);
    return;
  }

  // group tasks by course
  const courses = {};
  for (const task of tasks) {
    if (!courses[task.courseId]) {
      courses[task.courseId] = {
        name: task.courseName,
        color: task.courseColor,
        tasks: [],
      };
    }
    courses[task.courseId].tasks.push(task);
  }

  for (const { name, color, tasks: courseTasks } of Object.values(courses)) {
    const total = courseTasks.length;
    const done  = courseTasks.filter(t => t.status === "done").length;
    const pct   = total > 0 ? Math.round((done / total) * 100) : 0;

    const totalMins   = courseTasks.reduce((s, t) => s + (t.estimatedMinutes || 0), 0);
    const doneMins    = courseTasks.filter(t => t.status === "done")
                                   .reduce((s, t) => s + (t.estimatedMinutes || 0), 0);
    const spentMins   = courseTasks.reduce((s, t) => s + (t.timeSpent || 0), 0);

    const card = document.createElement("div");
    card.className = "progress-card";
    card.style.setProperty("--course-color", color || "#ccc");

    // course name
    const nameEl = document.createElement("div");
    nameEl.className = "progress-card-name";
    nameEl.textContent = name;
    card.appendChild(nameEl);

    // tasks stat
    const taskStat = document.createElement("div");
    taskStat.className = "progress-stat";

    const taskHeader = document.createElement("div");
    taskHeader.className = "progress-stat-header";

    const taskLabel = document.createElement("span");
    taskLabel.className = "progress-stat-label";
    taskLabel.textContent = "Tasks";

    const taskValue = document.createElement("span");
    taskValue.className = "progress-stat-value";
    taskValue.textContent = `${done} / ${total} done`;

    taskHeader.appendChild(taskLabel);
    taskHeader.appendChild(taskValue);
    taskStat.appendChild(taskHeader);

    const track = document.createElement("div");
    track.className = "progress-track";
    const fill = document.createElement("div");
    fill.className = "progress-bar-fill";
    fill.style.width = `${pct}%`;
    track.appendChild(fill);
    taskStat.appendChild(track);
    card.appendChild(taskStat);

    // time stat
    const timeStat = document.createElement("div");
    timeStat.className = "progress-stat";

    const timeLabel = document.createElement("div");
    timeLabel.className = "progress-stat-label";
    timeLabel.textContent = "Time";
    timeStat.appendChild(timeLabel);

    const timeRow = document.createElement("div");
    timeRow.className = "progress-time-row";

    const addTimeStat = (num, label, muted = false) => {
      const wrap = document.createElement("div");
      wrap.className = "progress-time-stat";
      const numEl = document.createElement("div");
      numEl.className = `progress-time-num${muted ? " muted" : ""}`;
      numEl.textContent = num;
      const labelEl = document.createElement("div");
      labelEl.className = "progress-time-label";
      labelEl.textContent = label;
      wrap.appendChild(numEl);
      wrap.appendChild(labelEl);
      timeRow.appendChild(wrap);
    };

    addTimeStat(spentMins > 0 ? formatMinutes(spentMins) : "—", "spent");
    addTimeStat(doneMins  > 0 ? formatMinutes(doneMins)  : "—", "done (est)", true);
    addTimeStat(totalMins > 0 ? formatMinutes(totalMins) : "—", "total (est)", true);

    timeStat.appendChild(timeRow);
    card.appendChild(timeStat);

    progressGrid.appendChild(card);
  }
}


// ---------------------------------------------------------------------------
// calendar
// ---------------------------------------------------------------------------
const calendarBtn   = document.getElementById("calendar-btn");
const calGrid       = document.getElementById("cal-grid");
const calWeekLabel  = document.getElementById("cal-week-label");
const calPrevBtn    = document.getElementById("cal-prev-btn");
const calTodayBtn   = document.getElementById("cal-today-btn");
const calNextBtn    = document.getElementById("cal-next-btn");

const CAL_DAYS_FROM_MON = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
const CAL_DAYS_FROM_SUN = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

let calWeekStart = getWeekStart(new Date());
let calTasks = [];

calendarBtn.addEventListener("click", async () => {
  showView("calendar");
  renderBreadcrumb("calendar");
  await loadCalendar();
});

async function loadCalendar() {
  const termId = termSelect.value || state.currentTermId;
  try {
    const data = await api(`/calendar${termId ? `?termId=${termId}` : ""}`);
    calTasks = data.tasks;
    renderCalendar();
  } catch (err) {
    console.error("failed to load calendar:", err);
  }
}

function getWeekStart(date) {
  const d = new Date(date);
  const day = d.getDay(); // 0=Sun … 6=Sat
  const start = state.weekStartDay; // 1=Mon, 0=Sun
  const diff = (day - start + 7) % 7;
  d.setDate(d.getDate() - diff);
  d.setHours(0, 0, 0, 0);
  return d;
}

function renderCalendar() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);

  const weekEnd = new Date(calWeekStart);
  weekEnd.setDate(weekEnd.getDate() + 6);

  const fmtOpts = { month: "long", day: "numeric" };
  calWeekLabel.textContent =
    `${calWeekStart.toLocaleDateString("en-US", fmtOpts)} – ` +
    `${weekEnd.toLocaleDateString("en-US", { ...fmtOpts, year: "numeric" })}`;

  calGrid.innerHTML = "";

  for (let i = 0; i < 7; i++) {
    const day = new Date(calWeekStart);
    day.setDate(day.getDate() + i);
    const dayStr = day.toISOString().slice(0, 10);
    const isToday = day.getTime() === today.getTime();

    const dayTasks = calTasks.filter(t => t.dueDate && t.dueDate.slice(0, 10) === dayStr);

    const col = document.createElement("div");
    col.className = `cal-day${isToday ? " today" : ""}`;
    col.dataset.date = dayStr;

    col.addEventListener("dragover", (e) => {
      e.preventDefault();
      col.classList.add("drag-over");
    });
    col.addEventListener("dragleave", (e) => {
      if (!col.contains(e.relatedTarget)) col.classList.remove("drag-over");
    });
    col.addEventListener("drop", (e) => {
      e.preventDefault();
      col.classList.remove("drag-over");
      const taskId = e.dataTransfer.getData("text/plain");
      const newDate = col.dataset.date;
      if (taskId && newDate) rescheduleTask(taskId, newDate);
    });

    const header = document.createElement("div");
    header.className = "cal-day-header";

    const dayName = document.createElement("div");
    dayName.className = "cal-day-name";
    dayName.textContent = (state.weekStartDay === 1 ? CAL_DAYS_FROM_MON : CAL_DAYS_FROM_SUN)[i];

    const dayNum = document.createElement("div");
    dayNum.className = "cal-day-num";
    dayNum.textContent = day.getDate();

    header.appendChild(dayName);
    header.appendChild(dayNum);
    col.appendChild(header);

    const tasksEl = document.createElement("div");
    tasksEl.className = "cal-tasks";

    if (dayTasks.length === 0) {
      const empty = document.createElement("div");
      empty.className = "cal-empty";
      empty.textContent = "—";
      tasksEl.appendChild(empty);
    } else {
      for (const task of dayTasks) {
        tasksEl.appendChild(buildCalTaskPill(task));
      }
    }

    col.appendChild(tasksEl);
    calGrid.appendChild(col);
  }
}

function buildCalTaskPill(task) {
  const pill = document.createElement("div");
  pill.className = `cal-task-pill${task.status === "done" ? " done" : ""}`;
  pill.style.setProperty("--task-color", task.courseColor || "#ccc");
  pill.draggable = true;
  pill.dataset.taskId = task.id;

  pill.addEventListener("dragstart", (e) => {
    e.dataTransfer.setData("text/plain", task.id);
    e.dataTransfer.effectAllowed = "move";
    setTimeout(() => pill.classList.add("dragging"), 0);
  });
  pill.addEventListener("dragend", () => pill.classList.remove("dragging"));

  pill.addEventListener("click", (e) => {
    if (pill.classList.contains("dragging")) return;
    openTaskModal(task, { fromCalendar: true });
  });

  const course = document.createElement("div");
  course.className = "cal-task-course";
  course.textContent = task.courseName || "";

  const title = document.createElement("div");
  title.className = "cal-task-title";
  title.textContent = task.title;

  pill.appendChild(course);
  pill.appendChild(title);

  if (task.estimatedMinutes) {
    const est = document.createElement("div");
    est.className = "cal-task-est";
    est.textContent = `~${formatMinutes(task.estimatedMinutes)}`;
    pill.appendChild(est);
  }

  return pill;
}

async function rescheduleTask(taskId, newDate) {
  // optimistic update
  const task = calTasks.find(t => t.id === taskId);
  if (!task || task.dueDate?.slice(0, 10) === newDate) return;
  task.dueDate = newDate;
  renderCalendar();

  try {
    await api(`/tasks/${taskId}`, {
      method: "PATCH",
      body: JSON.stringify({ dueDate: newDate }),
    });
  } catch (err) {
    console.error("failed to reschedule task:", err);
    // revert by reloading
    await loadCalendar();
  }
}

calPrevBtn.addEventListener("click", () => {
  calWeekStart = new Date(calWeekStart);
  calWeekStart.setDate(calWeekStart.getDate() - 7);
  renderCalendar();
});

calNextBtn.addEventListener("click", () => {
  calWeekStart = new Date(calWeekStart);
  calWeekStart.setDate(calWeekStart.getDate() + 7);
  renderCalendar();
});

calTodayBtn.addEventListener("click", () => {
  calWeekStart = getWeekStart(new Date());
  renderCalendar();
});


// ---------------------------------------------------------------------------
// study session timer
// ---------------------------------------------------------------------------
const timerPanel      = document.getElementById("timer-panel");
const timerTaskName   = document.getElementById("timer-task-name");
const timerDisplay    = document.getElementById("timer-display");
const timerProgressFill = document.getElementById("timer-progress-fill");
const timerStartBtn   = document.getElementById("timer-start-btn");
const timerResetBtn   = document.getElementById("timer-reset-btn");
const timerCloseBtn   = document.getElementById("timer-close-btn");
const timerDoneMsg    = document.getElementById("timer-done-msg");

let timer = {
  taskId: null,
  taskTitle: "",
  totalSeconds: 0,
  remaining: 0,
  running: false,
  intervalId: null,
};

function startTimerForTask(task) {
  // if a different task was running, stop it first
  if (timer.intervalId) clearInterval(timer.intervalId);

  timer.taskId    = task.id;
  timer.taskTitle = task.title;
  timer.totalSeconds = (state.workIntervalMinutes || 25) * 60;
  timer.remaining    = timer.totalSeconds;
  timer.running      = false;
  timer.intervalId   = null;

  timerDoneMsg.classList.add("hidden");
  timerTaskName.textContent = task.title;
  timerStartBtn.textContent = "Start";
  timerPanel.classList.remove("hidden");
  updateTimerDisplay();
  updateTimerProgress();
  refreshTimerButtons();
}

function updateTimerDisplay() {
  const m = Math.floor(timer.remaining / 60);
  const s = timer.remaining % 60;
  timerDisplay.textContent = `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

function updateTimerProgress() {
  const pct = timer.totalSeconds > 0
    ? ((timer.totalSeconds - timer.remaining) / timer.totalSeconds) * 100
    : 0;
  timerProgressFill.style.width = `${pct}%`;
}

function refreshTimerButtons() {
  timerStartBtn.textContent = timer.running ? "Pause" : "Start";
}

function refreshTaskTimerBtns() {
  document.querySelectorAll(".task-timer-btn").forEach(btn => {
    const id = btn.dataset.taskId;
    btn.textContent = (id === timer.taskId && timer.running) ? "⏸" : "▶";
    btn.classList.toggle("active", id === timer.taskId && timer.running);
  });
}

timerStartBtn.addEventListener("click", () => {
  if (timer.running) {
    // pause
    clearInterval(timer.intervalId);
    timer.intervalId = null;
    timer.running = false;
  } else {
    // start / resume
    timer.running = true;
    timer.intervalId = setInterval(() => {
      timer.remaining--;
      updateTimerDisplay();
      updateTimerProgress();
      if (timer.remaining <= 0) {
        clearInterval(timer.intervalId);
        timer.intervalId = null;
        timer.running = false;
        onTimerComplete();
      }
    }, 1000);
  }
  refreshTimerButtons();
  refreshTaskTimerBtns();
});

timerResetBtn.addEventListener("click", () => {
  clearInterval(timer.intervalId);
  timer.intervalId = null;
  timer.running = false;
  timer.remaining = timer.totalSeconds;
  timerDoneMsg.classList.add("hidden");
  updateTimerDisplay();
  updateTimerProgress();
  refreshTimerButtons();
  refreshTaskTimerBtns();
});

timerCloseBtn.addEventListener("click", () => {
  clearInterval(timer.intervalId);
  timer.intervalId = null;
  timer.running = false;
  timer.taskId = null;
  timerPanel.classList.add("hidden");
  refreshTaskTimerBtns();
});

async function onTimerComplete() {
  const elapsedMinutes = Math.round((timer.totalSeconds - timer.remaining) / 60);
  timerStartBtn.textContent = "Start";
  timerDoneMsg.classList.remove("hidden");
  refreshTaskTimerBtns();

  // auto-log time to the task
  if (timer.taskId && elapsedMinutes > 0) {
    try {
      const task = state.tasks.find(t => t.id === timer.taskId);
      const existing = task?.timeSpent || 0;
      const updated = await api(`/tasks/${timer.taskId}`, {
        method: "PATCH",
        body: JSON.stringify({ timeSpent: existing + elapsedMinutes }),
      });
      const idx = state.tasks.findIndex(t => t.id === timer.taskId);
      if (idx !== -1) {
        state.tasks[idx] = updated;
        const el = taskList.querySelector(`[data-task-id="${timer.taskId}"]`);
        if (el) el.replaceWith(buildTaskItem(updated));
        renderEstimateBanner(state.tasks, state.currentProject);
      }
    } catch (err) {
      console.error("failed to log timer time:", err);
    }
  }
}


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
