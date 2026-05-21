import { createRequire } from 'module';
import path from 'path';

class DependencyMissingError extends Error {
  constructor(packageName, projectRoot, cause) {
    const installCommand = `npm install -D ${packageName}`;
    super(
      `Missing project dependency "${packageName}". Install it in ${projectRoot} with: ${installCommand}`,
    );
    this.name = 'DependencyMissing';
    this.packageName = packageName;
    this.projectRoot = projectRoot;
    this.installCommand = installCommand;
    this.cause = cause;
  }
}

/**
 * Dynamically resolve a package from the user's project context.
 *
 * This function loads packages using require.createRequire() from the project
 * context, which ensures user-installed versions and their plugins are used.
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
    console.error(`[daemon] Using ${packageName} from project node_modules`);
    return resolved;
  } catch (e) {
    throw new DependencyMissingError(packageName, projectRoot, e);
  }
}

export { DependencyMissingError, resolvePackage };
