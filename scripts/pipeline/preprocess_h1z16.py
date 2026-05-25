from scripts.pipeline.preprocess import (
    DEFAULT_METER_URN as TARGET_METER_URN,
    apply_confirmed_physical_nan_rules,
    fetch_joined_data,
    get_numeric_columns,
    preprocess_meter,
    summarize_changed_nan_counts,
)


def preprocess_h1z16(
    print_progress: bool = True,
    print_issue_details: bool = True,
):
    return preprocess_meter(
        TARGET_METER_URN,
        print_progress=print_progress,
        print_issue_details=print_issue_details,
    )


def main() -> None:
    df, df_before, _, _ = preprocess_h1z16(
        print_progress=True,
        print_issue_details=True,
    )
    summarize_changed_nan_counts(df_before, df, TARGET_METER_URN)
    print("df.shape")
    print(df.shape)
    print()
    print("df.head()")
    print(df.head())


if __name__ == "__main__":
    main()
