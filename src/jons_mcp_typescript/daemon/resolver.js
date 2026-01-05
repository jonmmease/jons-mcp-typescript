import { createRequire } from 'module';
import path from 'path';

/**
 * Dynamically resolve a package from the user's project context.
 *
 * This function attempts to load a package using require.createRequire() from
 * the project context, which ensures that user-installed versions and their
 * plugins are used. Falls back to bundled version if not found.
 *
 * @param {string} packageName - The name of the package to resolve
 * @param {string} projectRoot - The root directory of the user's project
 * @returns {*} The resolved module
 *
 * @example
 * const projectRoot = process.cwd();
 * const prettier = resolvePackage('prettier', projectRoot);
 * const { ESLint } = resolvePackage('eslint', projectRoot);
 */
function resolvePackage(packageName, projectRoot) {
  try {
    // Create require from project context (resolved from package.json)
    const req = createRequire(path.join(projectRoot, 'package.json'));
    const resolved = req(packageName);
    console.log(`[daemon] Using ${packageName} from project node_modules`);
    return resolved;
  } catch (e) {
    console.warn(`[daemon] Using bundled ${packageName} (not found in project): ${e.message}`);
    // Fall back to bundled require (this will use the daemon's node_modules)
    // For ES modules, use the daemon's own require
    const bundledReq = createRequire(import.meta.url);
    return bundledReq(packageName);
  }
}

export { resolvePackage };
