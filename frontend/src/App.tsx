import { ReactFlowProvider } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useState } from "react";
import { fetchCatalog } from "./api";
import { CatalogProvider } from "./components/catalog";
import { Editor } from "./components/Editor";
import type { Catalog } from "./types";

export function App() {
  const [catalog, setCatalog] = useState<Catalog | null>(null);
  const [bootError, setBootError] = useState<string | null>(null);

  useEffect(() => {
    fetchCatalog()
      .then(setCatalog)
      .catch((error: unknown) => {
        setBootError(error instanceof Error ? error.message : "The API did not respond.");
      });
  }, []);

  if (bootError) {
    return (
      <main className="boot">
        <h1>Uniswap Wash Detector</h1>
        <p>The editor could not read the box catalog.</p>
        <p className="banner error">{bootError}</p>
        <p>Start the API from the repo root, then refresh:</p>
        <pre>washdetect serve</pre>
      </main>
    );
  }
  if (!catalog) {
    return (
      <main className="boot">
        <h1>Uniswap Wash Detector</h1>
        <p>Loading the box catalog…</p>
      </main>
    );
  }
  return (
    <CatalogProvider catalog={catalog}>
      <ReactFlowProvider>
        <Editor catalog={catalog} />
      </ReactFlowProvider>
    </CatalogProvider>
  );
}
