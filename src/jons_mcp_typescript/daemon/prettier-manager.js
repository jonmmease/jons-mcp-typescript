import path from 'path';
import { resolvePackage } from './resolver.js';

/**
 * PrettierManager handles code formatting with config caching
 * - Caches prettier configs by directory with a 5-minute TTL
 * - Handles prettier errors with proper error codes
 */
export class PrettierManager {
  constructor(projectRoot) {
    this.projectRoot = projectRoot;
    this.prettier = resolvePackage('prettier', projectRoot);
    this.configCache = new Map(); // dir -> { config, timestamp }
  }

  /**
   * Resolve and cache prettier config for a file
   * Caches by directory since prettier config applies to whole directories
   * Uses 5 minute TTL
   *
   * @param {string} filepath - The file path to resolve config for
   * @returns {Promise<object|null>} The prettier config object
   */
  async resolveConfig(filepath) {
    const dir = path.dirname(filepath);
    const cached = this.configCache.get(dir);
    const now = Date.now();
    const TTL = 5 * 60 * 1000; // 5 minutes

    // Return cached config if still valid
    if (cached && now - cached.timestamp < TTL) {
      return cached.config;
    }

    try {
      const config = await this.prettier.resolveConfig(filepath);
      this.configCache.set(dir, { config, timestamp: now });
      return config;
    } catch (error) {
      // Clear cache on error
      this.configCache.delete(dir);
      throw error;
    }
  }

  /**
   * Format code using prettier
   *
   * @param {string} filepath - The file path
   * @param {string} content - The code content to format
   * @returns {Promise<{formatted: string, changed: boolean}>} Formatted result
   */
  async format(filepath, content) {
    try {
      const config = await this.resolveConfig(filepath);
      const formatted = await this.prettier.format(content, {
        ...config,
        filepath,
      });
      return {
        formatted,
        changed: formatted !== content,
      };
    } catch (error) {
      // Re-throw to let daemon handle error codes
      this._enhanceError(error);
      throw error;
    }
  }

  /**
   * Check if code matches prettier formatting
   *
   * @param {string} filepath - The file path
   * @param {string} content - The code content to check
   * @returns {Promise<{formatted: boolean, diff?: string}>} Check result
   */
  async check(filepath, content) {
    try {
      const config = await this.resolveConfig(filepath);
      const isFormatted = await this.prettier.check(content, {
        ...config,
        filepath,
      });
      return {
        isFormatted,
      };
    } catch (error) {
      // Re-throw to let daemon handle error codes
      this._enhanceError(error);
      throw error;
    }
  }

  /**
   * Get the prettier config for a file
   *
   * @param {string} filepath - The file path
   * @returns {Promise<{config: object|null}>} The config object
   */
  async getConfig(filepath) {
    try {
      const config = await this.resolveConfig(filepath);
      return { config };
    } catch (error) {
      // Re-throw to let daemon handle error codes
      this._enhanceError(error);
      throw error;
    }
  }

  /**
   * Enhance error messages to help daemon identify error types
   * This helps the daemon's error handler categorize errors correctly
   *
   * @private
   * @param {Error} error - The error to enhance
   */
  _enhanceError(error) {
    // Identify parse errors
    if (
      error.message.includes('SyntaxError') ||
      error.message.includes('Parse') ||
      error.message.includes('Unexpected')
    ) {
      error.message = `Parse error: ${error.message}`;
    }

    // Identify config errors
    if (
      error.message.includes('Cannot find') ||
      error.message.includes('ENOENT') ||
      error.message.includes('No configuration file')
    ) {
      error.message = `Config error: ${error.message}`;
      error.path = error.filepath || '';
    }
  }
}
