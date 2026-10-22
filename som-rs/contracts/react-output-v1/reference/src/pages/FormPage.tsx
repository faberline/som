// Creates a row or edits one. This file is the same in every generated product.
import { useEffect, useState } from "react";
import type { SubmitEvent } from "react";
import { Link, useNavigate, useParams } from "react-router";
import type { ApiError } from "../api.ts";
import { createRow, getRow, patchRow } from "../api.ts";
import type { FormText } from "../form.ts";
import { bodyOf, initialCreateText, own, rowText } from "../form.ts";
import type { CreateBody, PatchBody } from "../schema.ts";
import { FIELDS, RESOURCE } from "../schema.ts";
import type { FieldSpec } from "../spec.ts";
import { idOf } from "./DetailPage.tsx";
import { ErrorView } from "./ErrorView.tsx";

function Control({
  field,
  value,
  onChange,
}: {
  field: FieldSpec;
  value: string;
  onChange: (value: string) => void;
}) {
  const id = `field-${field.name}`;
  if (field.type === "bool" && !field.nullable) {
    return (
      <input
        id={id}
        type="checkbox"
        checked={value === "true"}
        onChange={(event) => onChange(event.target.checked ? "true" : "false")}
      />
    );
  }
  if (field.type === "bool") {
    return (
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        <option value="">null</option>
        <option value="true">true</option>
        <option value="false">false</option>
      </select>
    );
  }
  const type = field.type === "date" ? "date" : "text";
  const mode =
    field.type === "int"
      ? "numeric"
      : field.type === "float"
        ? "decimal"
        : undefined;
  return (
    <input
      id={id}
      type={type}
      inputMode={mode}
      value={value}
      onChange={(event) => onChange(event.target.value)}
    />
  );
}

export function FormPage({ mode }: { mode: "create" | "edit" }) {
  const params = useParams();
  const navigate = useNavigate();
  const id = mode === "edit" ? idOf(params.id) : null;
  const [start, setStart] = useState<FormText | null>(
    mode === "create" ? initialCreateText(FIELDS) : null,
  );
  const [text, setText] = useState<FormText | null>(start);
  const [error, setError] = useState<ApiError | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (mode !== "edit" || id === null) {
      return;
    }
    let live = true;
    void getRow(id).then((result) => {
      if (!live) {
        return;
      }
      if (result.ok) {
        const loaded = rowText(FIELDS, result.value as Record<string, unknown>);
        setStart(loaded);
        setText(loaded);
      } else {
        setError(result.error);
      }
    });
    return () => {
      live = false;
    };
  }, [mode, id]);

  if (mode === "edit" && id === null) {
    return <p role="alert">Page not found.</p>;
  }

  const submit = async (event: SubmitEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (text === null || start === null) {
      return;
    }
    const body = bodyOf(FIELDS, text, start, mode === "create");
    if (id !== null && Object.keys(body).length === 0) {
      void navigate(`/${id}`);
      return;
    }
    setBusy(true);
    setError(null);
    const result =
      id === null
        ? await createRow(body as CreateBody)
        : await patchRow(id, body as PatchBody);
    setBusy(false);
    if (result.ok) {
      void navigate(`/${result.value.id}`);
    } else {
      setError(result.error);
    }
  };

  const title =
    id === null ? `New ${RESOURCE.name}` : `Edit ${RESOURCE.name} ${id}`;
  return (
    <section>
      <div className="toolbar">
        <h1>{title}</h1>
        <Link to={id === null ? "/" : `/${id}`}>Cancel</Link>
      </div>
      <ErrorView error={error} />
      {text === null && error === null && <p>Loading…</p>}
      {text !== null && (
        <form onSubmit={(event) => void submit(event)}>
          {FIELDS.map((field) => {
            const messages =
              error === null ? undefined : own(error.fieldErrors, field.name);
            return (
              <div key={field.name} className="field">
                <label htmlFor={`field-${field.name}`}>{field.name}</label>
                <Control
                  field={field}
                  value={own(text, field.name) ?? ""}
                  onChange={(value) =>
                    setText({ ...text, [field.name]: value })
                  }
                />
                {messages !== undefined && (
                  <ul className="field-errors">
                    {messages.map((message, index) => (
                      <li key={index}>{message}</li>
                    ))}
                  </ul>
                )}
              </div>
            );
          })}
          <button type="submit" disabled={busy}>
            Save
          </button>
        </form>
      )}
    </section>
  );
}
