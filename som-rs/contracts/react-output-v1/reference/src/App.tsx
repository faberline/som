// Routes of the generated front end. This file is the same in every generated product.
import { useEffect } from "react";
import { Link, Route, Routes } from "react-router";
import { DetailPage } from "./pages/DetailPage.tsx";
import { FormPage } from "./pages/FormPage.tsx";
import { ListPage } from "./pages/ListPage.tsx";
import { RESOURCE } from "./schema.ts";

export function App() {
  useEffect(() => {
    document.title = RESOURCE.name;
  }, []);
  return (
    <div className="app">
      <header>
        <Link to="/">{RESOURCE.name}</Link>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ListPage />} />
          <Route path="/new" element={<FormPage mode="create" />} />
          <Route path="/:id" element={<DetailPage />} />
          <Route path="/:id/edit" element={<FormPage mode="edit" />} />
          <Route path="*" element={<p role="alert">Page not found.</p>} />
        </Routes>
      </main>
    </div>
  );
}
