// Admin-editable category option-lists (F-A / FE-6).
//
// Screens must never ship their own copy of a category array — the admin edits these
// in Settings and the change has to land everywhere without a redeploy. This hook is
// the one place that fetches them, with the shipped defaults as the fallback so an
// older backend (or an offline moment) still renders a usable dropdown.
import { useEffect, useState } from "react";
import { getCategories } from "../api/settings";

/**
 * @param {string} family  one of expense | maintenance | complaint | menu | stock | vendor
 * @param {string[]} fallback  the shipped defaults, used until/unless the API answers
 */
export function useCategoryList(family, fallback = []) {
  const [list, setList] = useState(fallback);

  useEffect(() => {
    let alive = true;
    getCategories()
      .then((d) => {
        const items = d?.families?.[family];
        if (alive && Array.isArray(items) && items.length) setList(items);
      })
      .catch(() => {
        /* keep the shipped defaults */
      });
    return () => {
      alive = false;
    };
  }, [family]);

  return list;
}

/** snake_case slug -> "Snake Case", for display only. */
export const prettyCategory = (s) =>
  String(s || "")
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
