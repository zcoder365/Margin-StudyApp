/*
  margin — frontend app
  =====================
  handles google sign-in via firebase, talks to the flask backend with the
  user's ID token, and renders courses. no framework, just modules + DOM.
*/

// import the bits of firebase we need from google's CDN
// using the modular SDK (v10) — only ships what we actually use
import { initializeApp } from "https://www.gstatic.com/firebasejs/10.13.0/firebase-app.js";
import {
  getAuth,
  GoogleAuthProvider,
  signInWithPopup,
  signOut,
  onAuthStateChanged,
} from "https://www.gstatic.com/firebasejs/10.13.0/firebase-auth.js";

// ---------------------------------------------------------------------------
// firebase config — same one you grabbed from the firebase console.
// safe to commit; it's protected by firestore security rules, not by secrecy.
// ---------------------------------------------------------------------------
const firebaseConfig = {
  apiKey: "AIzaSyC59bJU9rhAezTFyfOSi138G9xgxXXM8oQ",
  authDomain: "margin-app-4c293.firebaseapp.com",
  projectId: "margin-app-4c293",
  storageBucket: "margin-app-4c293.firebasestorage.app",
  messagingSenderId: "358817583449",
  appId: "1:358817583449:web:7b392f1b84693cd3717c48",
};

const app = initializeApp(firebaseConfig);
const auth = getAuth(app);
const provider = new GoogleAuthProvider();

// backend URL — flask runs on 5000 in local dev. we'll swap this for the
// railway URL when we deploy (probably via an env var or config file).
const API_BASE = "http://localhost:5000/api";


// ---------------------------------------------------------------------------
// API helper — automatically attaches the firebase ID token to every request.
// every protected backend route expects this header, so centralizing it here
// means we never forget. one source of truth = fewer bugs.
// ---------------------------------------------------------------------------
async function apiRequest(path, options = {}) {
  // get the current user from firebase auth (might be null if signed out)
  const user = auth.currentUser;
  if (!user) {
    throw new Error("not signed in");
  }

  // grab a fresh ID token. firebase caches these for ~1hr and refreshes automatically.
  // we just ask for the current one — no manual refresh needed 🙌
  const idToken = await user.getIdToken();

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${idToken}`,
      ...(options.headers || {}),
    },
  });

  if (!response.ok) {
    // try to extract a helpful error message from the response body
    const errorBody = await response.json().catch(() => ({ error: "unknown error" }));
    throw new Error(errorBody.error || `request failed: ${response.status}`);
  }

  return response.json();
}


// ---------------------------------------------------------------------------
// DOM references — grab everything once at startup
// ---------------------------------------------------------------------------
const signedOutView = document.getElementById("signed-out-view");
const signedInView = document.getElementById("signed-in-view");
const userEmailEl = document.getElementById("user-email");
const signInBtn = document.getElementById("sign-in-btn");
const signOutBtn = document.getElementById("sign-out-btn");
const addCourseBtn = document.getElementById("add-course-btn");
const courseModal = document.getElementById("course-modal");
const courseNameInput = document.getElementById("course-name-input");
const courseColorInput = document.getElementById("course-color-input");
const cancelCourseBtn = document.getElementById("cancel-course-btn");
const saveCourseBtn = document.getElementById("save-course-btn");
const coursesList = document.getElementById("courses-list");
const emptyState = document.getElementById("empty-state");


// ---------------------------------------------------------------------------
// auth event handlers
// ---------------------------------------------------------------------------
signInBtn.addEventListener("click", async () => {
  try {
    // pops up a google sign-in window. firebase handles the OAuth dance for us.
    await signInWithPopup(auth, provider);
    // the onAuthStateChanged listener below handles UI updates after sign-in,
    // so we don't need to do anything else here
  } catch (err) {
    console.error("sign-in failed:", err);
    alert(`sign-in failed: ${err.message}`);
  }
});

signOutBtn.addEventListener("click", async () => {
  await signOut(auth);
  // again, onAuthStateChanged handles the UI swap
});


// onAuthStateChanged is firebase's "tell me whenever the auth state changes" event.
// fires on:
//   - app load (with the cached user, if any)
//   - sign in
//   - sign out
//   - token refresh
// this is THE central spot for "is the user logged in?" logic.
onAuthStateChanged(auth, async (user) => {
  if (user) {
    // user is signed in — show the app, hide the landing
    signedOutView.classList.add("hidden");
    signedInView.classList.remove("hidden");
    userEmailEl.textContent = user.email;

    // tell the backend "hey, this user just signed in" — creates their
    // firestore doc + default term if they're brand new (idempotent if not)
    try {
      await apiRequest("/users/init", {
        method: "POST",
        body: JSON.stringify({ displayName: user.displayName || "" }),
      });

      // then load their courses
      await loadCourses();
    } catch (err) {
      console.error("user init or course load failed:", err);
      alert(`oops: ${err.message}`);
    }
  } else {
    // user is signed out — show landing, hide app
    signedInView.classList.add("hidden");
    signedOutView.classList.remove("hidden");
    userEmailEl.textContent = "";
  }
});


// ---------------------------------------------------------------------------
// courses logic
// ---------------------------------------------------------------------------
async function loadCourses() {
  try {
    const data = await apiRequest("/courses");
    renderCourses(data.courses);
  } catch (err) {
    console.error("failed to load courses:", err);
  }
}

function renderCourses(courses) {
  // clear out any existing course cards (keep the empty state element)
  // we do this by removing all children except #empty-state, then re-adding cards
  coursesList.innerHTML = "";

  if (courses.length === 0) {
    // show empty state
    coursesList.appendChild(emptyState);
    emptyState.classList.remove("hidden");
    return;
  }

  // hide empty state and render course cards
  emptyState.classList.add("hidden");

  for (const course of courses) {
    const card = document.createElement("div");
    card.className = "course-card";
    // CSS variable trick: --course-color is read by the .course-card border-left rule.
    // sets the colored stripe on each card based on the course's chosen color 🎨
    card.style.setProperty("--course-color", course.color);

    const heading = document.createElement("h3");
    heading.textContent = course.name;
    card.appendChild(heading);

    coursesList.appendChild(card);
  }
}


// ---------------------------------------------------------------------------
// modal logic — for adding a new course
// ---------------------------------------------------------------------------
addCourseBtn.addEventListener("click", () => {
  courseNameInput.value = "";
  courseColorInput.value = "#a8d5ba";  // reset to default soft green
  courseModal.classList.remove("hidden");
  courseNameInput.focus();
});

cancelCourseBtn.addEventListener("click", () => {
  courseModal.classList.add("hidden");
});

saveCourseBtn.addEventListener("click", async () => {
  const name = courseNameInput.value.trim();
  if (!name) {
    alert("please give your course a name 💛");
    return;
  }

  // disable the button while saving so the user can't double-click
  saveCourseBtn.disabled = true;

  try {
    await apiRequest("/courses", {
      method: "POST",
      body: JSON.stringify({
        name,
        color: courseColorInput.value,
      }),
    });

    courseModal.classList.add("hidden");
    await loadCourses();   // refetch + re-render
  } catch (err) {
    console.error("failed to create course:", err);
    alert(`couldn't save: ${err.message}`);
  } finally {
    saveCourseBtn.disabled = false;
  }
});

// also let users press Enter in the name input to save
courseNameInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") saveCourseBtn.click();
});