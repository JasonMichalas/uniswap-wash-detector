import { createContext, useContext, type ReactNode } from "react";
import type { Catalog } from "../types";

const CatalogContext = createContext<Catalog | null>(null);

export function CatalogProvider({
  catalog,
  children,
}: {
  catalog: Catalog;
  children: ReactNode;
}) {
  return <CatalogContext.Provider value={catalog}>{children}</CatalogContext.Provider>;
}

export function useCatalog(): Catalog {
  const catalog = useContext(CatalogContext);
  if (!catalog) throw new Error("Catalog is not loaded.");
  return catalog;
}
