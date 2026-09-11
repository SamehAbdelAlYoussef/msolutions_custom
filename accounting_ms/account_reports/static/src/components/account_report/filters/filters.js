/** @odoo-module **/
/*
 * msolutions - Community-compatible accounting distribution.
 *
 * Base `AccountReportFilters` component. Report implementations either extend
 * it (`account_intrastat`, `account_fiscal_categories_fleet`) or patch its
 * prototype (`account_reports_cash_basis`), so the getters they build upon must
 * exist here:
 *
 *   * ``filterExtraOptionsData``  - extra filter definitions, keyed by option
 *   * ``selectedExtraOptions``    - human readable summary of active filters
 *
 * The class is intentionally defensive: it renders even when no controller
 * (and therefore no report options) has been injected yet.
 */
import { Component } from "@odoo/owl";

export class AccountReportFilters extends Component {
    static template = "account_reports.AccountReportFilters";
    static props = { "*": true };

    get controller() {
        return this.props.controller || this.env.controller || {};
    }

    get cachedFilterOptions() {
        return this.controller.cachedFilterOptions || {};
    }

    get filterExtraOptionsData() {
        return {};
    }

    get filterExtraOptions() {
        return Object.keys(this.filterExtraOptionsData);
    }

    get selectedExtraOptions() {
        return "";
    }
}

export default AccountReportFilters;
