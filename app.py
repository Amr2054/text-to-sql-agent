"""Run with: python -m streamlit run app.py"""

import os

import pandas as pd
import streamlit as st

from src.agent import build_agent


def record_update(run, node, update):
    """Collect real graph events for this question's display history."""
    if node == "load_schema":
        run["stage"] = "Generating SQL…"
    elif node == "generate_query":
        run["sql"] = update.get("generated_sql")
        run["result"] = None
        if update.get("error"):
            outcome = "Invalid model response"
        elif update.get("status") == "unsupported":
            outcome = "Unsupported"
        else:
            outcome = "Ready to execute"
        run["attempts"].append({
            "number": update["attempts"],
            "sql": update.get("generated_sql"),
            "outcome": outcome,
            "detail": update.get("error") or update.get("reason"),
        })
        run["stage"] = "Checking the decision…" if outcome != "Ready to execute" else "Running SQL…"
    elif node == "run_query":
        result = update["query_result"]
        run["result"] = result
        attempt = run["attempts"][-1]
        attempt["outcome"] = "Success" if result["ok"] else "SQL error"
        attempt["detail"] = (
            f"{len(result['rows'])} row(s) returned."
            if result["ok"] else result["error"]
        )
        run["stage"] = "Writing the answer…" if result["ok"] else "Reviewing the SQL error…"

    if "answer" in update:
        run["answer"] = update["answer"]
        run["outcome"] = {
            "generate_answer": "success",
            "report_unsupported": "unsupported",
            "report_failure": "failed",
        }[node]


def render_run(run, show_history):
    st.caption(f"Question: {run['question']}")
    if run["outcome"] == "failed":
        st.error(run["answer"])
    elif run["outcome"] == "unsupported":
        st.info(run["answer"])
    elif run["answer"]:
        with st.container(border=True):
            st.markdown(run["answer"])
    else:
        st.info(run["stage"])

    st.metric("Attempts", len(run["attempts"]))
    with st.expander("Generated SQL"):
        if run["sql"]:
            st.code(run["sql"], language="sql")
        else:
            st.caption("No executable SQL was generated.")

    with st.expander("Execution result"):
        result = run["result"]
        if result is None:
            st.caption("No query was executed for the latest decision.")
        elif not result["ok"]:
            st.error(result["error"])
        else:
            st.caption(f"{len(result['rows'])} row(s) returned.")
            frame = pd.DataFrame(result["rows"], columns=result["columns"])
            # Different tables may return columns with identical names.
            if not frame.columns.is_unique:
                frame.columns = [f"{name} ({i + 1})" for i, name in enumerate(frame.columns)]
            st.dataframe(frame, hide_index=True, width="stretch")
            if not result["rows"]:
                st.info("The query succeeded, but no records matched the filters.")

    if show_history:
        with st.expander("Correction history", expanded=True):
            if not run["attempts"]:
                st.caption("No model decision has been returned yet.")
            for attempt in run["attempts"]:
                st.markdown(f"**Attempt {attempt['number']} → {attempt['outcome']}**")
                if attempt["sql"]:
                    st.code(attempt["sql"], language="sql")
                if attempt["detail"]:
                    st.text(attempt["detail"])


def main():
    st.set_page_config(page_title="Chinook SQL Assistant", page_icon="🎵")
    st.title("Chinook SQL Assistant")
    st.write(
        "Ask questions about a music store's artists, albums, tracks, customers, "
        "and sales. Explore the answer, the SQL behind it, and any corrections."
    )
    st.caption("Read-only database · One question at a time")

    configured = bool(os.getenv("OPENROUTER_API_KEY"))
    if not configured:
        st.info("Add OPENROUTER_API_KEY to the project's .env file, then restart Streamlit.")

    with st.form("question_form"):
        question = st.text_area(
            "Your question",
            placeholder="Which five artists have the most albums?",
            height=100,
        )
        submitted = st.form_submit_button("Ask", type="primary", disabled=not configured)

    show_history = st.checkbox("Show correction history", value=False)
    output = st.empty()

    if submitted and not question.strip():
        st.warning("Enter a question first.")
    elif submitted:
        # This retains the displayed result across UI reruns, not conversation memory.
        run = {
            "question": question.strip(), "attempts": [], "sql": None,
            "result": None, "answer": None, "outcome": "running",
            "stage": "Loading the database schema…",
        }
        st.session_state["last_run"] = run
        with st.spinner("Working on your question…"):
            try:
                graph = build_agent()
                for event in graph.stream({
                    "question": run["question"], "attempts": 0, "error": None,
                }, stream_mode="updates"):
                    for node, update in event.items():
                        record_update(run, node, update)
                    with output.container():
                        render_run(run, show_history)
            except Exception as error:
                # Preserve any SQL/results already obtained if a service fails.
                run["outcome"] = "failed"
                run["answer"] = f"Could not complete this question: {error}"
                if run["attempts"] and run["attempts"][-1]["outcome"] == "Ready to execute":
                    run["attempts"][-1]["outcome"] = "Not completed"

    if "last_run" in st.session_state:
        with output.container():
            render_run(st.session_state["last_run"], show_history)


if __name__ == "__main__":
    main()
