import { useState } from "react";
import { fetchLesson, peekLesson } from "../utils/lessonStore.js";
import Loader from "./Loader.jsx";

function CodeBlock({ code }) {
  const [copied, setCopied] = useState(false);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code.snippet);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch (err) {
      // clipboard access can be blocked; the code can still be selected by hand
    }
  }

  return (
    <div className="lesson-code">
      <div className="lesson-code-bar">
        <span>{code.language}</span>
        <button type="button" onClick={copy}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
      <pre>
        <code>{code.snippet}</code>
      </pre>
    </div>
  );
}

// "Learn with AI": the learner picks subtopics, the AI explains them with examples, code and a mini project.
export default function TopicLearn({ topic, skill, hackathon, subtopics }) {
  const choices = subtopics.length > 0 ? subtopics : [topic];
  const [picked, setPicked] = useState(() => new Set());
  const [state, setState] = useState({ status: "idle" });

  const chosen = choices.filter((_, i) => picked.has(i));

  function toggle(i) {
    setPicked((current) => {
      const next = new Set(current);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });
  }

  async function explain() {
    const ready = peekLesson(topic, skill, chosen);
    if (ready) {
      setState({ status: "done", data: ready });
      return;
    }
    setState({ status: "loading" });
    try {
      setState({ status: "done", data: await fetchLesson(topic, skill, hackathon, chosen) });
    } catch (err) {
      setState({ status: "error", message: err.message || "Something went wrong" });
    }
  }

  if (state.status === "loading") {
    return (
      <div className="learn">
        <Loader />
        <p className="loading-note">
          The AI is writing explanations, examples and code for your {chosen.length === 1 ? "subtopic" : "subtopics"}.
          This usually takes 10 to 30 seconds.
        </p>
      </div>
    );
  }

  if (state.status === "done") {
    const { lessons, mini_project: project } = state.data;
    return (
      <div className="learn">
        <div className="learn-head">
          <strong>Learn with AI</strong>
          <button type="button" className="link-button" onClick={() => setState({ status: "idle" })}>
            Change selection
          </button>
        </div>
        {lessons.map((lesson, i) => (
          <section className="lesson" key={i}>
            <h5>{lesson.subtopic}</h5>
            <p>{lesson.explanation}</p>
            {lesson.examples.length > 0 && (
              <>
                <p className="lesson-label">Examples</p>
                <ul>
                  {lesson.examples.map((example, j) => (
                    <li key={j}>{example}</li>
                  ))}
                </ul>
              </>
            )}
            {lesson.code && <CodeBlock code={lesson.code} />}
          </section>
        ))}
        {project && (
          <section className="lesson project">
            <p className="lesson-label">Mini project idea</p>
            <h5>{project.title}</h5>
            <p>{project.idea}</p>
            {project.steps.length > 0 && (
              <ol>
                {project.steps.map((step, j) => (
                  <li key={j}>{step}</li>
                ))}
              </ol>
            )}
          </section>
        )}
        <p className="learn-note">AI-written lessons can contain mistakes. Run the code and check the details yourself.</p>
      </div>
    );
  }

  return (
    <div className="learn">
      <div className="learn-head">
        <strong>Choose what to learn</strong>
        <span>
          <button type="button" className="link-button" onClick={() => setPicked(new Set(choices.map((_, i) => i)))}>
            Select all
          </button>
          {" · "}
          <button type="button" className="link-button" onClick={() => setPicked(new Set())}>
            Clear
          </button>
        </span>
      </div>
      <div className="learn-choices">
        {choices.map((choice, i) => (
          <label key={i} className={picked.has(i) ? "checked" : ""}>
            <input type="checkbox" checked={picked.has(i)} onChange={() => toggle(i)} />
            {choice}
          </label>
        ))}
      </div>
      {state.status === "error" && <p className="status error">{state.message}</p>}
      <button type="button" className="pill accent" disabled={chosen.length === 0} onClick={explain}>
        {state.status === "error" ? "Try again" : `Explain ${chosen.length || ""} selected`.replace("  ", " ")}
      </button>
    </div>
  );
}
