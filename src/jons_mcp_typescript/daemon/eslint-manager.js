import path from 'path';
import { resolvePackage } from './resolver.js';

/**
 * ESLintManager handles linting and configuration retrieval with caching.
 *
 * This manager:
 * - Dynamically resolves ESLint from the project's node_modules
 * - Creates ESLint instances per-directory for monorepo support
 * - Lints files using lintText() for content-based analysis
 * - Retrieves ESLint configuration using calculateConfigForFile()
 * - Transforms ESLint results to a normalized format
 * - Handles errors with appropriate error codes
 */
export class ESLintManager {
  /**
   * Initialize ESLintManager with a project root.
   *
   * @param {string} projectRoot - The root directory of the project
   * @throws {Error} If ESLint cannot be resolved or instantiated
   */
  constructor(projectRoot) {
    this.projectRoot = projectRoot;
    this.ESLint = null;
    // Cache ESLint instances by directory for monorepo support
    this.instanceCache = new Map(); // dir -> { eslint, eslintWithFix }

    try {
      const { ESLint } = resolvePackage('eslint', projectRoot);
      this.ESLint = ESLint;
    } catch (error) {
      throw new Error(`Failed to initialize ESLint: ${error.message}`);
    }
  }

  /**
   * Get or create ESLint instances for a specific directory.
   * This enables proper config resolution in monorepos where each
   * package may have its own eslint.config.mjs.
   *
   * @param {string} dir - Directory to use as cwd for ESLint
   * @returns {{ eslint: ESLint, eslintWithFix: ESLint }}
   */
  getInstancesForDir(dir) {
    if (this.instanceCache.has(dir)) {
      return this.instanceCache.get(dir);
    }

    const instances = {
      eslint: new this.ESLint({
        cwd: dir,
        cache: true,
        fix: false,
      }),
      eslintWithFix: new this.ESLint({
        cwd: dir,
        cache: true,
        fix: true,
      }),
    };

    this.instanceCache.set(dir, instances);
    return instances;
  }

  /**
   * Lint file content and optionally apply fixes.
   *
   * @param {string} filepath - The file path (relative or absolute)
   * @param {string} content - The file content to lint
   * @param {boolean} [fix=false] - Whether to apply ESLint fixes
   * @returns {Promise<Object>} Linting result with issues, counts, and optional fixed code
   * @throws {Error} If linting fails due to configuration or plugin issues
   *
   * @example
   * const result = await manager.lint('src/index.ts', 'const x = 1;', false);
   * // Returns: { issues: [...], errorCount: 0, warningCount: 0, fixed: false, fixedCode: null }
   */
  async lint(filepath, content, fix = false) {
    try {
      // Get ESLint instance for the file's directory (monorepo support)
      const fileDir = path.dirname(path.resolve(filepath));
      const { eslint, eslintWithFix } = this.getInstancesForDir(fileDir);
      const eslintInstance = fix ? eslintWithFix : eslint;

      const results = await eslintInstance.lintText(content, {
        filePath: filepath,
      });

      // ESLint returns an array of results, we only have one file
      if (!results || results.length === 0) {
        throw new Error('ESLint returned no results');
      }

      const result = results[0];

      // Transform ESLint messages to our format
      const issues = result.messages.map((message) => ({
        ruleId: message.ruleId,
        severity: message.severity === 2 ? 'error' : 'warning',
        message: message.message,
        line: message.line,
        column: message.column,
        fixable: !!message.fix,
      }));

      return {
        messages: issues,  // Alias for compatibility
        issues,
        errorCount: result.errorCount,
        warningCount: result.warningCount,
        fixed: !!result.output,
        fixedCode: result.output || null,
        fixedContent: result.output || null,  // Alias for compatibility
      };
    } catch (error) {
      // Re-throw with context for proper error classification
      if (error.message.includes('Config')) {
        throw new Error(`ESLint config error: ${error.message}`);
      } else if (error.message.includes('Plugin')) {
        throw new Error(`ESLint plugin missing: ${error.message}`);
      }
      throw error;
    }
  }

  /**
   * Retrieve ESLint configuration for a given file.
   *
   * @param {string} filepath - The file path to get configuration for
   * @returns {Promise<Object>} Configuration object with the config property
   * @throws {Error} If configuration cannot be calculated
   *
   * @example
   * const result = await manager.getConfig('src/index.ts');
   * // Returns: { config: { rules: { ... }, ... } }
   */
  async getConfig(filepath) {
    try {
      // Get ESLint instance for the file's directory (monorepo support)
      const fileDir = path.dirname(path.resolve(filepath));
      const { eslint } = this.getInstancesForDir(fileDir);

      const config = await eslint.calculateConfigForFile(filepath);
      return { config };
    } catch (error) {
      // Re-throw with context for proper error classification
      if (error.message.includes('Config')) {
        throw new Error(`ESLint config error: ${error.message}`);
      } else if (error.message.includes('Plugin')) {
        throw new Error(`ESLint plugin missing: ${error.message}`);
      }
      throw error;
    }
  }
}
