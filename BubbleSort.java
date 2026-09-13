/**
 * 冒泡排序（Bubble Sort）Java 实现
 *
 * 原理：重复遍历待排序数组，依次比较相邻两个元素，
 *       若顺序错误（前者大于后者）则交换，直到没有再需要交换的元素为止。
 *
 * 时间复杂度：最坏 O(n^2)，最好 O(n)（已有序，加标志位优化后）
 * 空间复杂度：O(1)
 */
public class BubbleSort {

    /**
     * 冒泡排序（升序）
     *
     * @param arr 待排序数组
     */
    public static void bubbleSort(int[] arr) {
        if (arr == null || arr.length < 2) {
            return;
        }
        int n = arr.length;
        // 外层循环：控制比较的轮数，共 n-1 轮
        for (int i = 0; i < n - 1; i++) {
            // 优化标志位：若某一轮没有发生交换，说明已经有序，提前退出
            boolean swapped = false;
            // 内层循环：每一轮将最大的元素"冒泡"到末尾
            // 已排好的后 i 个元素无需再比较，故为 n - 1 - i
            for (int j = 0; j < n - 1 - i; j++) {
                if (arr[j] > arr[j + 1]) {
                    // 交换相邻两个元素
                    int temp = arr[j];
                    arr[j] = arr[j + 1];
                    arr[j + 1] = temp;
                    swapped = true;
                }
            }
            if (!swapped) {
                break;
            }
        }
    }

    /**
     * 打印数组
     */
    public static void printArray(int[] arr) {
        StringBuilder sb = new StringBuilder("[");
        for (int i = 0; i < arr.length; i++) {
            sb.append(arr[i]);
            if (i < arr.length - 1) {
                sb.append(", ");
            }
        }
        sb.append("]");
        System.out.println(sb.toString());
    }

    public static void main(String[] args) {
        int[] arr = {64, 34, 25, 12, 22, 11, 90};
        System.out.print("排序前: ");
        printArray(arr);

        bubbleSort(arr);

        System.out.print("排序后: ");
        printArray(arr);
    }
}
