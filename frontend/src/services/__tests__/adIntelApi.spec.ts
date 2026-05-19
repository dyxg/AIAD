import { afterEach, describe, expect, it, vi } from 'vitest'
import {
    ApiRequestError,
    toUserErrorMessage,
    waitTaskDone,
} from '../adIntelApi'

describe('adIntelApi service', () => {
    afterEach(() => {
        vi.restoreAllMocks()
    })

    it('maps typed API errors to user-friendly text', () => {
        const err = new ApiRequestError('server error', 'server_error', 500)

        expect(toUserErrorMessage(err)).toContain('服务端异常')
    })

    it('polls task status until success', async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce({
                ok: true,
                text: async () => JSON.stringify({ status: 'running' }),
            })
            .mockResolvedValueOnce({
                ok: true,
                text: async () => JSON.stringify({ status: 'success', summary: {} }),
            })

        vi.stubGlobal('fetch', fetchMock)

        const polled: string[] = []
        const result = await waitTaskDone(
            'task-001',
            (_, status) => {
                polled.push(status)
            },
            5,
            0,
        )

        expect(polled).toEqual(['running', 'success'])
        expect((result as any).status).toBe('success')
        expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('keeps polling after a transient failed status before success', async () => {
        const fetchMock = vi
            .fn()
            .mockResolvedValueOnce({
                ok: true,
                text: async () => JSON.stringify({ status: 'failed', message: 'temporary upstream error' }),
            })
            .mockResolvedValueOnce({
                ok: true,
                text: async () => JSON.stringify({ status: 'success', summary: {} }),
            })

        vi.stubGlobal('fetch', fetchMock)

        const polled: string[] = []
        const result = await waitTaskDone(
            'task-002',
            (_, status) => {
                polled.push(status)
            },
            5,
            0,
            2,
        )

        expect(polled).toEqual(['failed', 'success'])
        expect((result as any).status).toBe('success')
        expect(fetchMock).toHaveBeenCalledTimes(2)
    })

    it('retries transient polling request errors before failing the UI flow', async () => {
        const fetchMock = vi
            .fn()
            .mockRejectedValueOnce(new Error('socket hang up'))
            .mockResolvedValueOnce({
                ok: true,
                text: async () => JSON.stringify({ status: 'success', summary: {} }),
            })

        vi.stubGlobal('fetch', fetchMock)

        const result = await waitTaskDone(
            'task-003',
            undefined,
            5,
            0,
            2,
            2,
        )

        expect((result as any).status).toBe('success')
        expect(fetchMock).toHaveBeenCalledTimes(2)
    })
})
