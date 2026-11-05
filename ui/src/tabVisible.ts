import { createContext } from "react";

/** True while a panel's tab is the one on screen. Hidden tabs don't load 3D:
 *  parsing a 40 MB mesh off-screen freezes the page and drops drags. */
export const TabVisible = createContext(true);
