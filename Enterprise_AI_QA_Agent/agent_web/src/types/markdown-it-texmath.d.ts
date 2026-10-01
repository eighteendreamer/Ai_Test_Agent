declare module "markdown-it-texmath" {
  import type MarkdownIt from "markdown-it";
  import type { KatexOptions } from "katex";

  // Options consumed by markdown-it-texmath 1.0.0 (texmath.js).
  interface TexmathOptions {
    engine?: { renderToString: (tex: string, options?: KatexOptions) => string };
    delimiters?: string | string[];
    outerSpace?: boolean;
    katexOptions?: KatexOptions;
    macros?: KatexOptions["macros"];
  }

  export default function texmath(md: MarkdownIt, options?: TexmathOptions): void;
}
