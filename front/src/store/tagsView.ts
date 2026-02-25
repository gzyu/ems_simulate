import { ref } from 'vue';
import type { RouteLocationNormalized } from 'vue-router';

export interface TagView extends Partial<RouteLocationNormalized> {
    title?: string;
}

export const visitedViews = ref<TagView[]>([]);

export const addView = (view: RouteLocationNormalized) => {
    if (visitedViews.value.some(v => v.path === view.path)) return;
    visitedViews.value.push(
        Object.assign({}, view, {
            title: (view.meta.title as string) || (view.params.deviceName as string) || (view.name as string) || '标签页'
        })
    );
};

export const delView = (view: TagView): Promise<TagView[]> => {
    return new Promise(resolve => {
        const index = visitedViews.value.findIndex(v => v.path === view.path);
        if (index > -1) {
            visitedViews.value.splice(index, 1);
        }
        resolve([...visitedViews.value]);
    });
};

export const delOthersViews = (view: TagView): Promise<TagView[]> => {
    return new Promise(resolve => {
        visitedViews.value = visitedViews.value.filter(v => v.path === view.path);
        resolve([...visitedViews.value]);
    });
};

export const delAllViews = (): Promise<void> => {
    return new Promise(resolve => {
        visitedViews.value = [];
        resolve();
    });
};
